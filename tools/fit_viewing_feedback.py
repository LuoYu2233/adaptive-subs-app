"""Estimate selection-aware feedback parameters and score later clips without writing a learner profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from viewing.study import report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="CAT 导出的完整测量记录")
    parser.add_argument("table", type=Path, help="与试测文件指纹相同的难度表")
    parser.add_argument("-o", "--output", type=Path, help="直接保存 UTF-8 校准报告，默认输出到终端")
    parser.add_argument("--field", choices=["b_initial", "b_frequency"], default="b_initial")
    parser.add_argument("--train", type=Path, nargs="+", required=True, help="先前视频的观看记录")
    parser.add_argument("--holdout", type=Path, nargs="+", default=[], help="随后独立视频的观看记录")
    parser.add_argument("--half-life-days", type=float, default=90)
    parser.add_argument("--no-domain", action="store_true")
    parser.add_argument("--no-discount", action="store_true")
    parser.add_argument("--fixed-feedback", action="store_true")
    args = parser.parse_args()
    try:
        if args.output and args.output.resolve() in {p.resolve() for p in [args.record, args.table, *args.train, *args.holdout]}:
            raise ValueError("模型报告的输出路径不能覆盖测量、词表或观看记录")
        result = report(args.record, args.table, args.field, args.train, args.holdout, half_life=args.half_life_days,
                        domain_bias=not args.no_domain, viewing_discount=not args.no_discount, fit_feedback=not args.fixed_feedback)
        result["frozenAt"] = time.time_ns() // 1_000_000
        if result["frozenAt"] < result["trainingCutoff"]:
            raise ValueError("训练记录来自未来，请核对设备时间")
        payload = (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        if args.output is None:
            sys.stdout.buffer.write(payload)
        else:
            fd, name = tempfile.mkstemp(dir=args.output.parent)
            temp = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, args.output)
            finally:
                temp.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, OverflowError) as e:
        parser.exit(2, f"{e}\n")


if __name__ == "__main__":
    main()
