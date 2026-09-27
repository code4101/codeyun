from concurrent.futures import ThreadPoolExecutor

from backend.core.fanxiu.data_annotation.performance import counters, performance_summary


def test_summary_and_delta_preserve_snapshot():
    ctx = {}
    metric = counters(ctx)
    metric.record("ocr", 1.0, units=2)
    baseline = metric.snapshot()
    metric.record("ocr", 3.0, units=4)
    assert performance_summary(ctx)["ocr"] == {
        "count": 2, "seconds": 4.0, "mean": 2.0, "std": 1.0, "units": 6,
    }
    assert performance_summary(ctx, since=baseline)["ocr"] == {
        "count": 1, "seconds": 3.0, "mean": 3.0, "std": 0.0, "units": 4,
    }
    assert baseline["ocr"] == (1, 1.0, 1.0, 2)
    assert performance_summary({}) == {}


def test_worker_updates_are_not_lost():
    ctx = {}
    metric = counters(ctx)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: metric.record("layer0", 0.5, units=1), range(100)))
    assert performance_summary(ctx)["layer0"] == {
        "count": 100, "seconds": 50.0, "mean": 0.5, "std": 0.0, "units": 100,
    }
