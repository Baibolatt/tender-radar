# SMC/ICT Research Bot — контекст проекта

> Этот файл читается Claude Code при каждом запуске. После каждого значимого
> шага обновляй раздел «Текущий этап» и добавляй строку в «Журнал этапов».

## Текущий этап (2026-10-22)

**Базовая версия готова и закоммичена.** Ветка `feature/smc-research-bot`,
последний коммит `8345c73` («Add SMC/ICT research bot: causal signals,
backtest and chart markup»). В рабочем дереве — только правка этого файла,
она ещё не закоммичена. Тесты: 15/15 OK.

Что уже работает:

- загрузка BTC/USDT 15m через `ccxt` + CSV-кэш;
- причинные детекторы: свинги, BOS/CHoCH, FVG, Order Blocks, свипы;
- HTF-байас (1h, режимы `structure` / `ema`), выровнен по закрытию HTF;
- стратегия: HTF bias → sweep → CHoCH → вход по FVG, SL за свип, TP = RR 2;
- бар-за-баром бэктест + учёт комиссий/проскальзывания через `vectorbt`;
- графики light/dark в `charts/`, CSV-отчёты в `reports/`;
- 15 тестов на причинность (prefix-тест) в `tests/test_lookahead.py`.

Текущие данные: 2 880 свечей, `2026-09-22 15:45` → `2026-10-22 15:30` UTC
(30 дней). Это мало: 9 сделок — статистически незначимо.

Базовый прогон (не оптимизирован, весь кэш):

| Метрика | Значение |
|---|---|
| Сделок | 9 |
| Total Return | −0.96 % |
| Buy & Hold | −5.65 % |
| Max Drawdown | 2.40 % |
| Win Rate | 44.4 % |
| Profit Factor | 0.75 |
| Total Fees | 62.80 USDT |
| Старт | 10 000 USDT |

## Что дальше (предлагаемый порядок)

1. Скачать больше истории (`python main.py fetch --days 180`) и перезапустить
   baseline на длинном периоде — текущих 30 дней недостаточно.
2. Зафиксировать baseline-метрики в отчёте, прежде чем что-либо менять.
3. Проверить устойчивость параметров (`swing_length`, `fvg_max_age`,
   `max_bars_after_*`, `sl_buffer_atr`, `rr`) — walk-forward, а не подбор
   на одном периоде.
4. Разобрать убыточные сделки и влияние издержек: 62.80 USDT комиссий на
   9 сделок — существенная часть результата.
5. Любые изменения логики сопровождать зелёными causality-тестами.

## Команды

```bash
source .venv/bin/activate

python main.py fetch --days 180       # обновить кэш в data/cache/
python main.py markup --last 400      # график в charts/
python main.py signals                # сводка детекторов
python main.py backtest               # сделки и статистика
python main.py all --last 400         # всё сразу + график
python main.py validate               # сверка с smartmoneyconcepts
python -m unittest discover -s tests -v
```

Важно: `--last` задаёт окно **только для графика**. Бэктест всегда считается
по всему кэшу, а не по последним N свечам.

## Ключевые инварианты

- **Никакой реальной торговли.** Только сигналы, разметка и бэктест.
- **Никакого lookahead bias.** У каждого события есть `index` (где оно
  находится) и `confirm_index` (когда о нём можно узнать). Стратегия и
  бэктест видят только `confirm_index`.
- Свинг подтверждается через `swing_length` закрытых свечей справа
  (`confirm = index + n`).
- FVG подтверждается на баре после средней свечи (`confirm = middle + 1`).
- BOS/CHoCH и свип — на закрытии бара, который их сформировал.
- HTF-байас применяется только следующим LTF-барам после закрытия HTF-свечи.
- `smartmoneyconcepts` ретроспективен: используется только в `validate`,
  не как источник сигналов.
- Одна позиция одновременно; на баре входа проверяется только SL; если SL и
  TP внутри одного бара — срабатывает SL.

## Карта проекта

```text
config.py                 frozen dataclass со всеми параметрами
main.py                   CLI: fetch | signals | backtest | markup | validate | all
data/loader.py            ccxt, CSV-кэш, ресемплинг, валидация OHLCV
signals/
  swing.py                свинги + confirm_index
  structure.py            BOS / CHoCH и байас
  fvg.py                  FVG и история перекрытия
  ob.py                   Order Blocks
  sweep.py                свипы ликвидности
  detect.py               единый прогон детекторов
  reference.py            сверка с smartmoneyconcepts
strategy/
  htf.py                  HTF-байас по закрытию HTF-свечи
  smc_strategy.py         state machine сетапа
backtest/
  simulator.py            исполнение бар-за-баром
  metrics.py              vectorbt-портфель и статистика
  runner.py               прогон и слияние сделок
viz/chart.py              разметка matplotlib (light/dark)
tests/test_lookahead.py   prefix-тесты на причинность
```

## Модель

Все сессии Claude Code работают только на `deepseek-v4-flash`;
`deepseek-v4-pro[1m]` не используется.

- `~/.claude/settings.json`: `model` = `deepseek-v4-flash`; в `env` заданы
  `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` и
  `CLAUDE_CODE_SUBAGENT_MODEL` = `deepseek-v4-flash`.
- `~/.deepseek-claude.sh`: те же переменные указывают на flash (alias `dc`).
- `ANTHROPIC_BASE_URL` и `ANTHROPIC_AUTH_TOKEN` остаются в
  `~/.deepseek-claude.sh` и в этот файл не записываются.

## Журнал этапов

- **2026-10-22** — базовая версия: данные, детекторы, стратегия, бэктест,
  графики, 15 causality-тестов; коммит `8345c73`; baseline −0.96 % на 30 днях.
