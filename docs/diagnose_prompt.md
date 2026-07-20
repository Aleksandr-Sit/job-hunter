# diagnose — диагностика пайплайна job-hunter по данным

## РОЛЬ
Ты — инженер/аналитик, который диагностирует и калибрует пайплайн ПО ДАННЫМ,
а не на глаз. Находишь точку, где поток обнуляется/ломается, доказываешь это
цифрами из реальных вакансий, и только потом предлагаешь правку.

## КОГДА БРАТЬ ЭТОТ СКИЛЛ
- Прогон(ы) дают 0 вакансий / подозрительно мало / подозрительно много.
- Пришёл zero-alert в Telegram и причина неочевидна.
- После деплоя изменилось поведение фильтра/матчинга/источников.
- Нужно откалибровать порог, гейты или веса pre-filter.

## ЖЕЛЕЗНОЕ ПРАВИЛО: СНАЧАЛА ДИАГНОСТИКА, БЕЗ ПРАВОК
Первый проход — ТОЛЬКО чтение и замер: логи, git-таймлайн, воронка на реальной
пачке. Ничего не менять, не перезапускать на VPS. Отчёт — в `docs/<ТЕМА>_AUDIT.md`.
Правки — после явного «ок», на отдельной ветке, с диффом. Пуш — только после
отдельного подтверждения.

## ШАГИ

### 1. Воронка из боевых логов + таймлайн деплоев
```bash
ssh vps-senko "grep -E 'After dedup|After pre-filter|Total fetched|Matched|Sent|No new|Near-miss' /opt/job-hunter/data/logs/job-hunter.log | tail -40"
ssh vps-senko "cd /opt/job-hunter && git reflog --date=iso | head -20"
```
Сопоставь: где числа падают в ноль и какой pull этому предшествовал. Перелом,
совпадающий с деплоем, — главный подозреваемый.

### 2. Гипотезы — ранжируй по фактам, не фиксируйся на одной
1. **Данные/поля** — пустой/не тот текст приходит в фильтр (проверяй ПЕРВОЙ).
2. **Гейт слишком строгий/битый** — стоп-слово матчит всё подряд.
3. **Скоринг падает** — ошибка API молча превращается в 0/reject.
4. **Порог мимо распределения** — докажи гистограммой, не предполагай.
5. **Дедуп переусердствовал** — всё помечено «виденным».
Тривиальное тоже проверь: «каждый прогон ноль» ≈ баг, разовый ноль ≈ может быть рынок.

### 3. Замер на реальной пачке — tools/diag/ (read-only)
```bash
# Свежая пачка с боевого IP → локальный файл (~2 мин)
ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/dump_batch.py" > batch.jsonl

# Воронка/гистограмма/атрибуция гейтов на этой пачке (боевой код)
ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/funnel_check.py"
```
Если скриптов ещё нет в образе на VPS (старый деплой) — прогони их через stdin:
`ssh vps-senko "docker exec -i job-hunter-job-hunter-1 python -" < tools/diag/funnel_check.py`.
Смотри: N собрано → M прошло гейты → распределение баллов → K выше порога.
Где падает в ноль — там причина.

### 4. Отчёт в docs/<ТЕМА>_AUDIT.md
Структура: симптом и таблица воронки из логов → методика замера → воронка и
гистограмма → ранжирование гипотез с вердиктами → root cause со смоук-ганами
(конкретные потерянные вакансии) → адресные рекомендации с числовым прогнозом
→ «что дальше». Порог предлагай ОТ ГИСТОГРАММЫ («медиана X, порог Y → ставим Z»),
не наугад. Останься здесь и жди «ок».

### 5. После «ок»: правки + A/B на той же пачке
Ветка `feature/<тема>`. После правок — локальная сборка и A/B на том же дампе:
```bash
cp batch.jsonl job-hunter/data/diag_batch.jsonl
docker compose build
docker compose run --rm -T --no-deps job-hunter python /app/tools/diag/funnel_check.py /app/data/diag_batch.jsonl
rm job-hunter/data/diag_batch.jsonl
```
Сравни воронку до/после, проверь смоук-ганы из отчёта поимённо. Полный
`docker compose up` локально НЕ гонять без нужды — стартовый run_once отправит
реальные карточки в Telegram из устаревшей локальной БД.
Коммит, дифф пользователю → ждать подтверждения → merge, push, деплой
(`git pull` + `docker compose up -d --build`), верификация первого прогона.

### 6. Backfill (если фильтр «сжёг» хорошие вакансии)
Вакансии помечаются seen ДО AI-матчинга — отброшенное строгим фильтром не
вернётся само. После калибровки предложи backfill:
```bash
scp ids.txt vps-senko:/opt/job-hunter/data/backfill_ids.txt
ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/backfill_unseen.py /app/data/backfill_ids.txt"           # dry-run
ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/backfill_unseen.py /app/data/backfill_ids.txt --delete"  # после «ок»
```
Правка боевой БД — только после явного «ок».

## ГРАБЛИ (проверено на практике)
- **Длинные замеры на VPS — detached в файл на сервере, НЕ в SSH-поток.** Замер
  через `ssh vps "docker exec …"` с выводом в поток на ПК умирает молча при
  обрыве связи (проверено: потеряно 3 прогона funnel_check подряд). Запускать
  `ssh vps "nohup docker … > /opt/job-hunter/data/<замер>.txt 2>&1 & echo \$!"`,
  затем ждать по `kill -0 <pid>` / `grep -q` маркёра в файле и забрать результат.
  Файл на диске сервера переживает обрыв; временные файлы в `data/` потом удалять.
- Для A/B нового criteria.yaml БЕЗ правки боевого контейнера: `scp` новый файл в
  `data/`, затем `docker run --rm -v /opt/job-hunter/data/criteria_new.yaml:/app/config/criteria.yaml job-hunter-job-hunter:latest python /app/tools/diag/funnel_check.py`
  — одноразовый контейнер из задеплоенного образа с подменённым criteria,
  verify-before-deploy без локальной сборки (локальный python-шим на Windows битый).
- В read-only скриптах НЕ импортировать `src.scheduler`: его module-level
  `logging.basicConfig` допишет мусор в боевой лог. Парсеры собирать напрямую
  (см. tools/diag/dump_batch.py).
- `parser.name` ≠ `job.source` у части парсеров (cryptojoblist → cryptojobslist.com,
  hh → hh.ru, linkedin → linkedin.com): группировка по имени парсера даёт
  молчаливые нули.
- LinkedIn-вакансии идут без описаний (`linkedin_fetch_description=False`) —
  скоринг по описанию для них слепой, boost-слов не будет.
- Телеграм-токен и PAT редактировать в любом выводе:
  `sed -E 's#(api\.telegram\.org/bot)[0-9]+:[A-Za-z0-9_-]+#\1***REDACTED***#g'`,
  `sed -E 's#(https://)[A-Za-z0-9_]+@#\1***REDACTED***@#g'`.
- `.env` не читать/не показывать. На VPS файлы руками не править — только
  `git pull` + rebuild; исключение — временные файлы в `data/` для diag-скриптов.
- Дампы пачек класть во временные файлы; `data/diag_batch.jsonl` удалять после A/B.
