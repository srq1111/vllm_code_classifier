#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对多个 JSON/JSONL 判定文件按 ID 投票。"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from risk_json_utils import (
    COMMON_ID_KEYS,
    COMMON_RESULT_KEYS,
    auto_get,
    canonical_result,
    normalize_id,
    read_json_or_jsonl,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对多个 JSONL 结果文件按 ID 进行多数投票。"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="两个或更多待投票的 JSON/JSONL 文件",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="voted_results.jsonl",
        help="投票结果输出，默认 voted_results.jsonl",
    )
    parser.add_argument(
        "--id-key",
        help="输入文件的 ID 字段，支持 meta.id；默认自动识别",
    )
    parser.add_argument(
        "--result-key",
        help="输入文件的判定字段，支持 meta.label；默认自动识别",
    )
    parser.add_argument(
        "--output-id-key",
        default="id",
        help="输出 ID 字段名，默认 id",
    )
    parser.add_argument(
        "--output-result-key",
        default="result",
        help="输出判定字段名，默认 result",
    )
    parser.add_argument(
        "--include-details",
        action="store_true",
        help="在主输出中附加票数、参与文件数、一致率和缺失文件",
    )
    parser.add_argument(
        "--tie-policy",
        choices=("skip", "first", "smallest", "largest", "error"),
        default="skip",
        help=(
            "平票处理：skip=跳过并报告；first=采用最早输入文件的结果；"
            "smallest/largest=按规范化结果排序选择；error=发现平票即失败。"
        ),
    )
    parser.add_argument(
        "--tie-report",
        default="tie_report.jsonl",
        help="平票报告，默认 tie_report.jsonl",
    )
    parser.add_argument(
        "--missing-report",
        help="可选：输出未在全部文件中出现的 ID 详情",
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="只对所有文件中都存在的 ID 投票",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=1,
        help="每个 ID 至少需要的有效票数，默认 1",
    )
    return parser.parse_args()


def load_result_file(
    path: str,
    id_key: str | None,
    result_key: str | None,
) -> OrderedDict[str, tuple[Any, Any]]:
    rows = read_json_or_jsonl(path)
    mapping: OrderedDict[str, tuple[Any, Any]] = OrderedDict()

    for row_no, row in enumerate(rows, start=1):
        try:
            raw_id, _ = auto_get(row, id_key, COMMON_ID_KEYS, "ID")
            result, _ = auto_get(
                row,
                result_key,
                COMMON_RESULT_KEYS,
                "判定结果",
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"{path}: 第 {row_no} 条记录读取失败：{exc}"
            ) from exc

        normalized_id = normalize_id(raw_id)
        if normalized_id in mapping:
            raise ValueError(
                f"{path}: ID {raw_id!r} 重复出现；"
                "同一文件中每个 ID 只能投一票。"
            )
        mapping[normalized_id] = (raw_id, result)

    return mapping


def choose_tie_winner(
    tied_results: list[str],
    vote_sequence: list[str],
    policy: str,
) -> str | None:
    if policy == "skip":
        return None
    if policy == "first":
        for result in vote_sequence:
            if result in tied_results:
                return result
        return None
    if policy == "smallest":
        return min(tied_results)
    if policy == "largest":
        return max(tied_results)
    if policy == "error":
        raise RuntimeError("tie")
    raise ValueError(f"未知平票策略：{policy}")


def main() -> None:
    args = parse_args()

    if len(args.inputs) < 2:
        raise SystemExit("至少需要提供两个结果文件。")
    if args.min_votes < 1:
        raise SystemExit("--min-votes 必须大于或等于 1。")
    if args.min_votes > len(args.inputs):
        raise SystemExit("--min-votes 不能大于输入文件数量。")

    mappings: list[OrderedDict[str, tuple[Any, Any]]] = []
    for path in args.inputs:
        try:
            mappings.append(
                load_result_file(path, args.id_key, args.result_key)
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for mapping in mappings:
        for normalized_id in mapping:
            if normalized_id not in seen:
                seen.add(normalized_id)
                ordered_ids.append(normalized_id)

    output_rows: list[dict[str, Any]] = []
    tie_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    skipped_require_all = 0
    skipped_min_votes = 0

    for normalized_id in ordered_ids:
        votes: list[tuple[int, Any, str]] = []
        missing_files: list[str] = []
        raw_id: Any = normalized_id
        raw_result_by_canonical: dict[str, Any] = {}
        vote_sequence: list[str] = []

        for file_index, (path, mapping) in enumerate(
            zip(args.inputs, mappings)
        ):
            if normalized_id not in mapping:
                missing_files.append(Path(path).name)
                continue

            current_raw_id, raw_result = mapping[normalized_id]
            raw_id = current_raw_id
            canonical = canonical_result(raw_result)
            votes.append((file_index, raw_result, canonical))
            vote_sequence.append(canonical)
            raw_result_by_canonical.setdefault(canonical, raw_result)

        if missing_files:
            missing_rows.append({
                "id": raw_id,
                "present_votes": len(votes),
                "missing_files": missing_files,
            })

        if args.require_all and missing_files:
            skipped_require_all += 1
            continue
        if len(votes) < args.min_votes:
            skipped_min_votes += 1
            continue

        counts = Counter(canonical for _, _, canonical in votes)
        max_votes = max(counts.values())
        winners = [
            canonical
            for canonical, count in counts.items()
            if count == max_votes
        ]

        if len(winners) > 1:
            tie_rows.append({
                "id": raw_id,
                "max_votes": max_votes,
                "tied_results": [
                    raw_result_by_canonical[item]
                    for item in winners
                ],
                "votes": dict(counts),
                "file_results": {
                    Path(args.inputs[file_index]).name: raw_result
                    for file_index, raw_result, _ in votes
                },
                "missing_files": missing_files,
            })

            try:
                winning_canonical = choose_tie_winner(
                    winners,
                    vote_sequence,
                    args.tie_policy,
                )
            except RuntimeError:
                write_jsonl(args.tie_report, tie_rows)
                raise SystemExit(
                    f"发现平票 ID={raw_id!r}，已停止处理；"
                    f"平票报告：{args.tie_report}"
                )

            if winning_canonical is None:
                continue
        else:
            winning_canonical = winners[0]

        winning_raw_result = raw_result_by_canonical[winning_canonical]
        row: dict[str, Any] = {
            args.output_id_key: raw_id,
            args.output_result_key: winning_raw_result,
        }

        if args.include_details:
            row.update({
                "votes": dict(counts),
                "winning_votes": max_votes,
                "total_votes": len(votes),
                "agreement": round(max_votes / len(votes), 6),
                "missing_files": missing_files,
            })

        output_rows.append(row)

    write_jsonl(args.output, output_rows)
    write_jsonl(args.tie_report, tie_rows)

    if args.missing_report:
        write_jsonl(args.missing_report, missing_rows)

    print(f"输入文件数：{len(args.inputs)}")
    print(f"全部候选 ID：{len(ordered_ids)}")
    print(f"成功输出 ID：{len(output_rows)}")
    print(f"平票 ID：{len(tie_rows)}")
    print(f"未在全部文件出现的 ID：{len(missing_rows)}")
    print(f"因 --require-all 跳过：{skipped_require_all}")
    print(f"因 --min-votes 跳过：{skipped_min_votes}")
    print(f"投票结果：{args.output}")
    print(f"平票报告：{args.tie_report}")
    if args.missing_report:
        print(f"缺失详情：{args.missing_report}")


if __name__ == "__main__":
    main()
