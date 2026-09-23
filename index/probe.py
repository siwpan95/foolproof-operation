"""实盘基准探针：卷 × 记录数 × 命中 × 耗时（**只读**，不改卷、不写索引）。

用途：阶段 2 的黄金值证据生成器 —— 把「USN/MFT 全量枚举在本机现盘的真实表现」
固化成可重跑的对照表，供 ``docs/usn-indexer.md`` 记账与后续阶段取基准。

用法::

    python -m index.probe                                  # 所有 NTFS 盘，每卷最多 60s
    python -m index.probe --volumes G: E: --seconds 45     # 指定卷与时长
    python -m index.probe --max-records 400000             # 限制内存占用（超限即截断）
    python -m index.probe --json bench.json                # 同时落 JSON 便于记账

输出列：卷 / 状态 / journal 窗口 / 记录数 / 速率 / 目录数 / 命中 / 样例路径 / 耗时。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

from .paths import PathResolver
from .usn import (
    JournalNotActiveError,
    UsnError,
    UsnVolume,
    VolumeAccessError,
    list_ntfs_volumes,
    normalize_volume,
)

#: 环境发现关心的事实名（阶段 4 的扫描判据就是这几类）。
DEFAULT_MATCHES = ("python.exe", "pythonw.exe", "pyvenv.cfg", "conda-meta", "site-packages")
DEFAULT_SECONDS = 60.0
DEFAULT_MAX_RECORDS = 600_000


def probe_volume(
    volume: str,
    *,
    seconds: float = DEFAULT_SECONDS,
    max_records: int = DEFAULT_MAX_RECORDS,
    matches: tuple = DEFAULT_MATCHES,
    sample_paths: int = 3,
) -> Dict[str, Any]:
    """枚举单卷并返回基准数据（只读）。失败以 ``status`` 如实报告，不抛栈。

    命中与目录数在**枚举过程中**计数，因此不受记录上限影响（截断时也只统计已扫过的前缀，
    由 ``truncated`` 标注）；只有 ``sample_paths > 0`` 时才建内存索引表（受 ``max_records``
    约束），设为 0 即得到**内存恒定**的完整总数测量。
    """
    volume = normalize_volume(volume)
    wanted = {name.lower(): name for name in matches}
    hits: Dict[str, int] = {name: 0 for name in matches}
    samples: Dict[str, List[str]] = {name: [] for name in matches}
    result: Dict[str, Any] = {
        "volume": volume,
        "status": "ok",
        "journal_id": None,
        "journal_span": None,
        "records": 0,
        "rate": 0.0,
        "directories": 0,
        "skipped": 0,
        "truncated": False,
        "resolved_samples": sample_paths > 0,
        "hits": {},
        "samples": {},
        "seconds": 0.0,
        "error": "",
    }
    started = time.monotonic()
    try:
        with UsnVolume(volume) as vol:
            info = vol.query_journal()
            if info is not None:
                result["journal_id"] = info.journal_id
                result["journal_span"] = info.journal_span
            resolver: Optional[PathResolver] = PathResolver(volume) if sample_paths > 0 else None
            directories = 0
            record_count = 0
            deadline = started + seconds
            for record in vol.enumerate_mft(deadline=deadline, limit=max_records):
                record_count += 1
                if record.is_directory:
                    directories += 1
                matched = wanted.get(record.name.lower())
                if matched is not None:
                    hits[matched] += 1
                    if resolver is not None and len(samples[matched]) < 200:
                        samples[matched].append(str(record.index))
                if resolver is not None:
                    resolver.add(record)
            result["records"] = record_count
            result["directories"] = directories
            result["skipped"] = vol.records_skipped
            result["truncated"] = record_count >= max_records
            result["calls"] = vol.calls
            result["hits"] = dict(hits)
            # 路径还原放到枚举结束后统一做（MFT 按 FRN 序，父目录可能晚于子项出现）
            if resolver is not None:
                resolved: Dict[str, List[str]] = {}
                for name, indices in samples.items():
                    paths: List[str] = []
                    for index in indices:
                        path = resolver.resolve(int(index))
                        if path:
                            paths.append(path)
                        if len(paths) >= sample_paths:
                            break
                    resolved[name] = paths
                result["samples"] = resolved
                result["index_nodes"] = len(resolver)
    except VolumeAccessError as exc:
        result["status"] = "拒绝访问"
        result["error"] = str(exc)
    except JournalNotActiveError as exc:
        result["status"] = "无 journal"
        result["error"] = str(exc)
    except UsnError as exc:
        result["status"] = "错误"
        result["error"] = str(exc)
    result["seconds"] = round(time.monotonic() - started, 2)
    result["rate"] = round(result["records"] / result["seconds"], 0) if result["seconds"] else 0.0
    return result


def _format_row(r: Dict[str, Any]) -> str:
    if r["status"] != "ok":
        return "| {volume} | {status} | - | - | - | - | - | {error} | {seconds}s |".format(**r)
    hits_text = " ".join(
        "{0}={1}".format(name.replace(".exe", ""), count)
        for name, count in r["hits"].items()
        if count
    ) or "-"
    fields = dict(r)
    fields["trunc"] = "（截断）" if r["truncated"] else ""
    fields["span"] = "{0:,}".format(r["journal_span"]) if r["journal_span"] is not None else "-"
    fields["hits_text"] = hits_text
    return (
        "| {volume} | ok{trunc} | {span} | {records:,} | {rate:,.0f}/s | {directories:,} "
        "| {hits_text} | {seconds}s |"
    ).format(**fields)


def bench_store(
    volume: str,
    index_dir: str,
    *,
    seconds: float = DEFAULT_SECONDS,
    max_records: int = 0,
    matches: tuple = DEFAULT_MATCHES,
    queries: int = 200,
    force: bool = True,
) -> Dict[str, Any]:
    """建库 + 查询基准（阶段 3 的黄金值）：落库吞吐与查询延迟。"""
    from .service import IndexService

    volume = normalize_volume(volume)
    service = IndexService(
        None,
        {"volumes": [volume], "index_dir": index_dir, "max_records": max_records or None},
    )
    result: Dict[str, Any] = {
        "volume": volume,
        "status": "ok",
        "build_seconds": 0.0,
        "records": 0,
        "rows_per_second": 0.0,
        "index_seconds": 0.0,
        "db_bytes": 0,
        "query_ms_avg": 0.0,
        "query_ms_max": 0.0,
        "resolve_ms_avg": 0.0,
        "hits": {},
        "error": "",
    }
    try:
        state = service.build(volume, force=force, seconds=seconds)
        if state.get("status") != "ready":
            result["status"] = state.get("status", "error")
            result["error"] = state.get("detail", "")
            return result
        store = service.store(volume)
        stats = store.stats()
        result["build_seconds"] = state.get("seconds", 0.0)
        result["records"] = stats.records
        result["rows_per_second"] = state.get("rows_per_second", 0.0)
        result["index_seconds"] = float(store.get_meta("index_build_seconds", "0") or 0)
        result["db_bytes"] = stats.db_bytes
        result["journal_id"] = stats.journal_id
        result["next_usn"] = stats.next_usn
        # 查询延迟：重复名字查询 N 次取平均/最大
        latencies: List[float] = []
        for _ in range(max(1, queries)):
            started = time.monotonic()
            found = store.find_by_names(matches)
            latencies.append((time.monotonic() - started) * 1000.0)
        result["query_ms_avg"] = round(sum(latencies) / len(latencies), 2)
        result["query_ms_max"] = round(max(latencies), 2)
        result["hits"] = {name: len(nodes) for name, nodes in found.items()}
        # 路径还原延迟（父链懒回溯）：对全部命中做一次
        resolve_latencies: List[float] = []
        resolved = 0
        for nodes in found.values():
            for node in nodes:
                started = time.monotonic()
                if store.resolve_path(node.index):
                    resolved += 1
                resolve_latencies.append((time.monotonic() - started) * 1000.0)
        if resolve_latencies:
            result["resolve_ms_avg"] = round(
                sum(resolve_latencies) / len(resolve_latencies), 3
            )
        result["resolved"] = resolved
    except (UsnError, VolumeAccessError) as exc:
        result["status"] = "拒绝访问" if isinstance(exc, VolumeAccessError) else "错误"
        result["error"] = str(exc)
    finally:
        service.stop()
    return result


def _format_store_row(r: Dict[str, Any]) -> str:
    if r["status"] != "ok":
        return "| {volume} | {status} | - | - | - | - | - | {error} |".format(**r)
    fields = dict(r)
    fields["mb"] = "{0:.1f}".format(r["db_bytes"] / 1048576.0)
    fields["hits_text"] = " ".join(
        "{0}={1}".format(name.replace(".exe", ""), count) for name, count in r["hits"].items() if count
    ) or "-"
    return (
        "| {volume} | {records:,} | {rows_per_second:,.0f}/s | {build_seconds}s | {mb} MB "
        "| {query_ms_avg} / {query_ms_max} ms | {resolve_ms_avg} ms | {hits_text} |"
    ).format(**fields)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m index.probe",
        description="USN/MFT 只读基准探针（不改卷、不创建 journal、不写仓库）",
    )
    parser.add_argument("--volumes", nargs="*", default=None, help="卷列表（默认全部 NTFS 盘）")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="每卷枚举上限秒数")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS, help="每卷记录数上限")
    parser.add_argument("--match", nargs="*", default=list(DEFAULT_MATCHES), help="命中的文件名")
    parser.add_argument("--samples", type=int, default=3, help="每类命中的样例路径条数")
    parser.add_argument("--json", default=None, help="结果落 JSON 文件的路径")
    parser.add_argument("--store", default=None, help="建库基准：索引目录（会写该目录）")
    parser.add_argument("--queries", type=int, default=200, help="建库基准：查询次数")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("USN 探针仅支持 Windows/NTFS（当前平台: {0}）".format(sys.platform))
        return 2
    volumes = args.volumes or list_ntfs_volumes()
    if not volumes:
        print("未发现 NTFS 卷")
        return 1

    if args.store:
        print("# 建库/查询基准（写索引到 {0}）  时间: {1}".format(args.store, time.strftime("%Y-%m-%d %H:%M:%S")))
        print("# 每卷上限 {0}s / {1:,} 条；查询名: {2}；重复查询 {3} 次".format(
            args.seconds, args.max_records, ", ".join(args.match), args.queries))
        print("")
        print("| 卷 | 记录数 | 落库速率 | 建库耗时 | 库大小 | 查询 平均/最大 | 还原 平均 | 命中 |")
        print("|---|---|---|---|---|---|---|---|")
        store_results = []
        for volume in volumes:
            row = bench_store(
                volume,
                args.store,
                seconds=args.seconds,
                max_records=args.max_records,
                matches=tuple(args.match),
                queries=args.queries,
            )
            store_results.append(row)
            print(_format_store_row(row), flush=True)
            if row["status"] != "ok":
                print("  - 提示：{0}".format(row["error"]), flush=True)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(store_results, fh, ensure_ascii=False, indent=2)
            print("\nJSON 已写入: {0}".format(args.json))
        return 0

    print("# USN/MFT 基准探针（只读）  时间: {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    print("# 每卷上限 {0}s / {1:,} 条；命中名: {2}".format(args.seconds, args.max_records, ", ".join(args.match)))
    print("")
    print("| 卷 | 状态 | journal 窗口 | 记录数 | 速率 | 目录数 | 命中 | 耗时 |")
    print("|---|---|---|---|---|---|---|---|")
    results = []
    for volume in volumes:
        row = probe_volume(
            volume,
            seconds=args.seconds,
            max_records=args.max_records,
            matches=tuple(args.match),
            sample_paths=args.samples,
        )
        results.append(row)
        print(_format_row(row), flush=True)
        if row["status"] == "拒绝访问":
            print("  - 提示：{0}".format(row["error"]), flush=True)
    print("")
    for row in results:
        if row["status"] == "ok":
            for name, paths in row["samples"].items():
                if paths:
                    print("[{0}] {1} 样例:".format(row["volume"], name))
                    for path in paths:
                        print("  - {0}".format(path))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print("\nJSON 已写入: {0}".format(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
