"""Full Criteo audit. Run from repository root with uv run --group eda."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "timestamp uid campaign conversion conversion_timestamp conversion_id attribution "
    "click click_pos click_nb cost cpo time_since_last_click "
    "cat1 cat2 cat3 cat4 cat5 cat6 cat7 cat8 cat9"
).split()


def connect(path=":memory:"):
    db = duckdb.connect(str(path))
    db.execute("SET memory_limit='1GB'")
    db.execute("SET threads=2")
    db.execute("SET preserve_insertion_order=false")
    return db


def quality_sql():
    return """SELECT count(*) rows,
    count(*) FILTER (WHERE conversion NOT IN (0,1)) invalid_conversion,
    count(*) FILTER (WHERE click NOT IN (0,1)) invalid_click,
    count(*) FILTER (WHERE attribution NOT IN (0,1)) invalid_attribution,
    count(*) FILTER (WHERE cost<0 OR NOT isfinite(cost)) invalid_cost,
    count(*) FILTER (WHERE cost=0) zero_cost,
    count(*) FILTER (WHERE cpo<0 OR NOT isfinite(cpo)) invalid_cpo,
    count(*) FILTER (WHERE conversion=1 AND (conversion_id<0 OR conversion_timestamp<timestamp)) invalid_positive,
    count(*) FILTER (WHERE conversion=0 AND (conversion_id<>-1 OR conversion_timestamp<>-1)) invalid_negative,
    count(*) FILTER (WHERE conversion=1 AND conversion_timestamp-timestamp>2592000) beyond_window,
    count(*) FILTER (WHERE attribution=1 AND conversion=0) attribution_without_conversion
    FROM d"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, default=ROOT / "data/raw/criteo_attribution_dataset.tsv.gz"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "reports/eda")
    parser.add_argument("--database", type=Path, default=ROOT / "data/eda.duckdb")
    args = parser.parse_args()
    output = args.output
    tables, figures = output / "tables", output / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    db = connect(args.database)
    db.execute(f"SET temp_directory='{ROOT / 'data/tmp'}'")
    with gzip.open(args.input, "rt") as stream:
        header = stream.readline().strip().split("\t")
    if header != FIELDS:
        raise ValueError(f"Unexpected header: {header}")
    types = {c: "DOUBLE" if c in ("cost", "cpo") else "BIGINT" for c in FIELDS}
    print("Reading all rows into local DuckDB", flush=True)
    db.execute("DROP TABLE IF EXISTS d")
    db.execute(
        "CREATE TABLE d AS SELECT * FROM read_csv(?, delim='\\t', header=true, columns=?)",
        [str(args.input), types],
    )

    def save(name, sql):
        print(name, flush=True)
        frame = db.sql(sql).df()
        frame.to_csv(tables / f"{name}.csv", index=False)
        return frame

    summary = save(
        "summary",
        """SELECT count(*) rows, count(DISTINCT uid) users,
    count(DISTINCT campaign) campaigns, min(timestamp) min_timestamp, max(timestamp) max_timestamp,
    sum(conversion) positive_rows, avg(conversion) positive_rate, sum(click) clicks,
    sum(attribution) attributed_rows, count(DISTINCT (uid,conversion_id,conversion_timestamp)) FILTER(WHERE conversion=1 AND conversion_id>=0) conversions,
    count(DISTINCT conversion_id) FILTER(WHERE conversion=1) raw_conversion_ids, sum(cost) total_cost FROM d""",
    )
    s = summary.iloc[0].to_dict()
    for k, v in list(s.items()):
        s[k] = float(v)
    end = int(s["max_timestamp"]) + 1
    cut1, cut2 = int(end * 0.6), int(end * 0.8)
    db.execute(
        f"CREATE OR REPLACE VIEW split AS SELECT *, CASE WHEN timestamp<{cut1} THEN 'train' WHEN timestamp<{cut2} THEN 'validation' ELSE 'test' END part FROM d"
    )
    quality = save("quality", quality_sql())
    save("schema", "DESCRIBE d")
    save(
        "field_quality",
        " UNION ALL ".join(
            f"SELECT '{c}' field, count(*) FILTER(WHERE {c} IS NULL) nulls, count(*) FILTER(WHERE {c}=-1) minus_one, count(*) FILTER(WHERE {c}=0) zeros, count(DISTINCT {c}) cardinality FROM d"
            for c in FIELDS
        ),
    )
    daily = save(
        "daily",
        """SELECT timestamp//86400 AS day_index, count(*) impressions, sum(conversion) positive_rows,
    count(DISTINCT (uid,conversion_id,conversion_timestamp)) FILTER(WHERE conversion=1) conversions, sum(click) clicks,
    count(DISTINCT campaign) campaigns, sum(cost) cost FROM d GROUP BY day_index ORDER BY day_index""",
    )
    campaigns = save(
        "campaigns",
        """SELECT campaign, count(*) impressions, sum(conversion) positive_rows,
    count(DISTINCT (uid,conversion_id,conversion_timestamp)) FILTER(WHERE conversion=1) conversions, sum(cost) cost,
    count(DISTINCT timestamp//86400) active_days FROM d GROUP BY campaign ORDER BY impressions DESC""",
    )
    save(
        "campaign_support",
        """SELECT count(*) campaigns,
    count(*) FILTER(WHERE conversions=0) no_conversions,
    count(*) FILTER(WHERE conversions<10) fewer_than_10,
    count(*) FILTER(WHERE conversions<100) fewer_than_100 FROM
    (SELECT campaign, count(DISTINCT (uid,conversion_id,conversion_timestamp)) FILTER(WHERE conversion=1) conversions FROM d GROUP BY campaign)""",
    )
    save(
        "splits",
        """SELECT part, count(*) impressions, sum(conversion) positive_rows,
    avg(conversion) positive_rate, count(DISTINCT uid) users,
    count(DISTINCT campaign) campaigns, count(DISTINCT (uid,conversion_id,conversion_timestamp)) FILTER(WHERE conversion=1) conversions,
    min(timestamp) start, max(timestamp) finish FROM split GROUP BY part ORDER BY start""",
    )
    save(
        "split_overlap",
        " UNION ALL ".join(
            f"SELECT '{a}' first_part, '{b}' second_part, '{field}' entity, count(*) overlap FROM (SELECT DISTINCT {field} FROM split WHERE part='{a}' AND {predicate} INTERSECT SELECT DISTINCT {field} FROM split WHERE part='{b}' AND {predicate})"
            for a, b in [("train", "validation"), ("train", "test"), ("validation", "test")]
            for field in ["uid", "conversion_id", "(uid,conversion_id,conversion_timestamp)"]
            for predicate in ["uid>=0" if field == "uid" else "conversion_id>=0"]
        ),
    )
    delay = save(
        "delay_quantiles",
        """SELECT min(conversion_timestamp-timestamp) minimum,
    quantile_cont(conversion_timestamp-timestamp,[0.25,0.5,0.75,0.9,0.95,0.99]) quantiles,
    max(conversion_timestamp-timestamp) maximum FROM d WHERE conversion=1""",
    )
    del delay
    delays = save(
        "delay_hist",
        """SELECT (conversion_timestamp-timestamp)//86400 delay_day,
    count(*) positive_rows FROM d WHERE conversion=1 GROUP BY delay_day ORDER BY delay_day""",
    )
    save(
        "feedback",
        f"""SELECT count(*) impressions,
    count(*) FILTER(WHERE timestamp+2592000<={end}) complete_windows_before_stream_end,
    count(*) FILTER(WHERE conversion=1 AND conversion_timestamp<={end}) positive_rows_observed_before_end,
    count(*) FILTER(WHERE conversion=1 AND conversion_timestamp>{end}) positive_rows_after_end,
    count(*) FILTER(WHERE conversion=1 AND conversion_timestamp>={cut1} AND timestamp<{cut1}) train_outcomes_after_validation_start
    FROM d""",
    )
    save(
        "conversion_consistency",
        """SELECT count(*) events,
    count(*) FILTER(WHERE users>1) multiple_users,
    count(*) FILTER(WHERE times>1) multiple_timestamps,
    count(*) FILTER(WHERE campaigns>1) multiple_campaigns,
    max(touches) max_touches, avg(touches) mean_touches
    FROM (SELECT conversion_id, count(*) touches, count(DISTINCT uid) users,
    count(DISTINCT conversion_timestamp) times, count(DISTINCT campaign) campaigns
    FROM d WHERE conversion=1 GROUP BY conversion_id)""",
    )
    save(
        "event_key_audit",
        """SELECT count(*) events,
    count(*) FILTER(WHERE campaigns>1) multi_campaign_events,
    count(*) FILTER(WHERE times>1) inconsistent_times
    FROM (SELECT uid,conversion_id,count(DISTINCT campaign) campaigns,
    count(DISTINCT conversion_timestamp) times FROM d WHERE conversion=1 GROUP BY uid,conversion_id)""",
    )
    save(
        "contact_distribution",
        " UNION ALL ".join(
            f"SELECT '{label}' entity, count(*) entities, avg(n) mean_contacts, quantile_cont(n,[0.5,0.9,0.99]) quantiles, max(n) max_contacts FROM (SELECT {field},count(*) n FROM d {where} GROUP BY {field})"
            for label, field, where in [
                ("user", "uid", ""),
                ("user_campaign", "(uid,campaign)", ""),
                ("conversion", "(uid,conversion_id,conversion_timestamp)", "WHERE conversion=1"),
            ]
        ),
    )
    save(
        "duplicates",
        "SELECT coalesce(sum(n-1),0) duplicate_rows FROM (SELECT count(*) n FROM d GROUP BY ALL COLUMNS(*) HAVING count(*)>1)".replace(
            "GROUP BY ALL COLUMNS(*)", "GROUP BY " + ",".join(FIELDS)
        ),
    )
    save(
        "cost_quantiles",
        " UNION ALL ".join(
            f"SELECT '{field}' field, min({field}) minimum, quantile_cont({field},[0.01,0.25,0.5,0.75,0.95,0.99,0.999]) quantiles,max({field}) maximum FROM d"
            for field in ["cost", "cpo"]
        ),
    )
    cat = save(
        "category_shift",
        " UNION ALL ".join(
            f"SELECT '{c}' feature, part, count(*) rows, count(DISTINCT {c}) categories, count(*) FILTER(WHERE {c} NOT IN(SELECT DISTINCT {c} FROM split WHERE part='train')) unseen_rows FROM split GROUP BY part"
            for c in FIELDS
            if c.startswith("cat")
        ),
    )
    # Only the plot uses a deterministic sample; all tables above cover the full dataset.
    costs = db.sql(
        "SELECT cost FROM d ORDER BY hash(timestamp,uid,campaign,cost,42),timestamp,uid,campaign,cost LIMIT 100000"
    ).df()
    sns.set_theme(style="whitegrid")

    def plot(name, title, draw):
        fig, ax = plt.subplots(figsize=(10, 5))
        draw(ax)
        ax.set_title(title)
        fig.tight_layout()
        fig.savefig(figures / f"{name}.png", dpi=130)
        plt.close(fig)

    plot(
        "daily",
        "Трафик по дням (весь датасет)",
        lambda ax: ax.plot(daily.day_index, daily.impressions, marker="o"),
    )
    plot(
        "daily_outcomes",
        "Положительные строки и уникальные события по дням",
        lambda ax: daily.set_index("day_index")[["positive_rows", "conversions"]].plot(ax=ax),
    )
    plot(
        "delay",
        "Задержка конверсии (положительные строки)",
        lambda ax: ax.bar(delays.delay_day, delays.positive_rows),
    )
    ranked = campaigns.sort_values("conversions", ascending=False).reset_index(drop=True)
    plot(
        "concentration",
        "Накопленная доля конверсий по кампаниям",
        lambda ax: ax.plot(
            range(1, len(ranked) + 1), ranked.conversions.cumsum() / ranked.conversions.sum()
        ),
    )
    plot(
        "cost",
        "Стоимость: детерминированная выборка 100 000 строк",
        lambda ax: (
            ax.hist(costs.loc[costs.cost > 0, "cost"], bins=100),
            ax.set_yscale("log"),
            ax.set_xlabel("cost, условные единицы"),
        ),
    )
    plot(
        "unseen",
        "Доля строк с категориями, отсутствующими в train",
        lambda ax: sns.barplot(
            data=cat[cat.part != "train"].assign(share=lambda x: x.unseen_rows / x.rows),
            x="feature",
            y="share",
            hue="part",
            ax=ax,
        ),
    )
    with args.input.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "file": args.input.name,
        "bytes": args.input.stat().st_size,
        "sha256": sha,
        "rows": int(s["rows"]),
        "split_boundaries_seconds": [cut1, cut2],
        "memory_limit": "1GB",
        "threads": 2,
        "duckdb_version": duckdb.__version__,
        "plot_sample_rows": 100000,
        "seed": 42,
    }
    source_path = ROOT / "data/raw/source_api.json"
    if (
        source_path.exists()
        and args.input.resolve() == (ROOT / "data/raw/criteo_attribution_dataset.tsv.gz").resolve()
    ):
        source = json.loads(source_path.read_text())
        manifest["source_revision"] = source["sha"]
        manifest["url"] = (
            f"https://huggingface.co/datasets/criteo/criteo-attribution-dataset/resolve/{source['sha']}/{args.input.name}"
        )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    assert int(daily.impressions.sum()) == int(s["rows"])
    assert int(campaigns.impressions.sum()) == int(s["rows"])
    assert int(daily.positive_rows.sum()) == int(s["positive_rows"])
    assert int(quality.iloc[0]["rows"]) == int(s["rows"])
    print(json.dumps(s, indent=2), flush=True)
    db.close()


if __name__ == "__main__":
    main()
