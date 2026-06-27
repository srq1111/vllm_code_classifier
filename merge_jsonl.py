#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
严格合并两个 JSONL 文件。

前提：
  1. 第一个文件内部不能有重复 ID
  2. 第二个文件内部不能有重复 ID
  3. 两个文件之间不能存在相同 ID

全部检查通过后，按“第一个文件在前、第二个文件在后”的顺序合并。

示例：
  python3 merge_jsonl.py part1.jsonl part2.jsonl merged.jsonl
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
from typing import Any

from risk_json_utils import (
    COMMON_ID_KEYS,
    auto_get,
    normalize_id,
    read_json_or_jsonl,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在确保 ID 不重复的前提下合并两个 JSONL 文件。"
    )
    parser.add_argument("first", help="第一个 JSONL 文件")
    parser.add_argument("second", help="第二个 JSONL 文件")
    parser.add_argument("output", help="合并后的 JSONL 文件")
    parser.add_argument(
        "--first-id-key",
        help="第一个文件的 ID 字段，支持 meta.id；默认自动识别",
    )
    parser.add_argument(
        "--second-id-key",
        help="第二个文件的 ID 字段，支持 meta.id；默认自动识别",
    )
    parser.add_argument(
        "--overlap-report",
        default="merge_overlap_report.jsonl",
        help="发现跨文件重复 ID 时的报告文件，默认 merge_overlap_report.jsonl",
    )
    parser.add_argument(
        "--allow-overwrite-output",
        action="store_true",
        help="允许覆盖已存在的输出文件",
    )
    return parser.parse_args()


def collect_ids(
    rows: list[Any],
    explicit_key: str | None,
    source_name: str,
) -> tuple[list[str], dict[str, Any], dict[str, list[int]]]:
    normalized_ids: list[str] = []
    raw_ids: dict[str, Any] = {}
    positions: dict[str, list[int]] = OrderedDict()

    for index, row in enumerate(rows, start=1):
        try:
            raw_id, _ = auto_get(
                row,
                explicit_key,
                COMMON_ID_KEYS,
                f"{source_name} ID",
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"{source_name} 第 {index} 条记录无法读取 ID：{exc}"
            ) from exc

        normalized = normalize_id(raw_id)
        normalized_ids.append(normalized)
        raw_ids.setdefault(normalized, raw_id)
        positions.setdefault(normalized, []).append(index)

    return normalized_ids, raw_ids, positions


def duplicate_details(
    positions: dict[str, list[int]],
    raw_ids: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "id": raw_ids[normalized_id],
            "count": len(record_numbers),
            "record_numbers": record_numbers,
        }
        for normalized_id, record_numbers in positions.items()
        if len(record_numbers) > 1
    ]


def main() -> None:
    args = parse_args()

    output_path = Path(args.output)
    if output_path.exists() and not args.allow_overwrite_output:
        raise SystemExit(
            f"输出文件已存在：{output_path}\n"
            "如需覆盖，请增加 --allow-overwrite-output"
        )

    first_rows = read_json_or_jsonl(args.first)
    second_rows = read_json_or_jsonl(args.second)

    try:
        (
            first_id_list,
            first_raw_ids,
            first_positions,
        ) = collect_ids(
            first_rows,
            args.first_id_key,
            f"第一个文件 {args.first}",
        )
        (
            second_id_list,
            second_raw_ids,
            second_positions,
        ) = collect_ids(
            second_rows,
            args.second_id_key,
            f"第二个文件 {args.second}",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    first_duplicates = duplicate_details(
        first_positions,
        first_raw_ids,
    )
    second_duplicates = duplicate_details(
        second_positions,
        second_raw_ids,
    )

    first_set = set(first_id_list)
    second_set = set(second_id_list)
    overlap_ids = [
        normalized_id
        for normalized_id in first_positions
        if normalized_id in second_set
    ]

    if first_duplicates or second_duplicates or overlap_ids:
        report_rows: list[dict[str, Any]] = []

        for item in first_duplicates:
            report_rows.append({
                "problem": "duplicate_id_in_first_file",
                **item,
            })

        for item in second_duplicates:
            report_rows.append({
                "problem": "duplicate_id_in_second_file",
                **item,
            })

        for normalized_id in overlap_ids:
            report_rows.append({
                "problem": "same_id_in_both_files",
                "id": first_raw_ids[normalized_id],
                "first_record_numbers": first_positions[normalized_id],
                "second_record_numbers": second_positions[normalized_id],
            })

        write_jsonl(args.overlap_report, report_rows)

        print(f"第一个文件记录数：{len(first_rows)}")
        print(f"第二个文件记录数：{len(second_rows)}")
        print(f"第一个文件内部重复 ID：{len(first_duplicates)}")
        print(f"第二个文件内部重复 ID：{len(second_duplicates)}")
        print(f"两个文件之间相同 ID：{len(overlap_ids)}")
        print(f"问题报告：{args.overlap_report}")
        raise SystemExit(
            "检测到重复 ID，已停止合并，未生成输出文件。"
        )

    merged_rows = first_rows + second_rows
    write_jsonl(output_path, merged_rows)

    print(f"第一个文件记录数：{len(first_rows)}")
    print(f"第二个文件记录数：{len(second_rows)}")
    print(f"合并后记录数：{len(merged_rows)}")
    print("重复 ID 检查：通过")
    print(f"输出文件：{output_path}")


if __name__ == "__main__":
    main()
