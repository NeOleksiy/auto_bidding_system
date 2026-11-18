# EDA Criteo

Источник — [Criteo Attribution Modeling for Bidding Dataset](https://huggingface.co/datasets/criteo/criteo-attribution-dataset).
Полный анализ и графики с выводами: [ноутбук](../../notebooks/01_criteo_eda.ipynb).

## Основные результаты

- 16 468 027 показов, 22 поля, 675 кампаний. Период — 30 дней 22 часа.
- NULL и полных дубликатов не найдено. В 53,26% строк нет истории прошлого клика (`-1`).
- 806 196 положительных строк соответствуют 438 730 событиям по ключу
  `(uid, conversion_id, conversion_timestamp)`. Один `conversion_id` не всегда уникален.
- У 279 кампаний меньше 100 конверсий: результаты малых кампаний нужно оценивать отдельно.
- Медианная задержка конверсии — 3,74 дня, p95 — 25,13 дня. Для проверки UCB
  с реальным ожиданием результатов нужна более длинная история.
- В данных только состоявшиеся показы и преобразованные цены. Оценка ставок
  через `cost` будет приближённой.

## Как повторить

```bash
uv sync --locked --group eda
uv run --group eda python scripts/download_data.py
uv run --group eda python scripts/run_eda.py
uv run --group eda jupyter lab notebooks/01_criteo_eda.ipynb
```

`tables/` — агрегаты по всему набору, `figures/` — графики.
Только гистограмма стоимости использует фиксированную выборку 100 000 строк.
Ревизия источника и контрольная сумма — в [manifest.json](manifest.json).
Исходные данные хранятся в `data/` вне Git. Модели пока не обучались.

Лицензия данных: CC BY-NC-SA 4.0. Авторы: Eustache Diemert, Julien Meynet,
Pierre Galland, Damien Lefortier; [статья](https://arxiv.org/abs/1707.06409).
