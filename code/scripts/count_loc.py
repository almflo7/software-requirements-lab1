"""统计非空、非纯注释的 Python 源代码行数。"""

from pathlib import Path


def count_file(path: Path) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    paths = sorted(
        path
        for folder in ("app", "scripts")
        for path in (root / folder).rglob("*.py")
        if "generated_gui" not in path.parts
    )
    total = 0
    for path in paths:
        amount = count_file(path)
        total += amount
        print(f"{path.relative_to(root)}: {amount}")
    print(f"TOTAL: {total}")
    return 0 if total > 1500 else 1


if __name__ == "__main__":
    raise SystemExit(main())
