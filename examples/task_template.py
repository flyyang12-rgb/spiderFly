"""Upload as a single Python file. Uses only the standard library."""
import os
from pathlib import Path


def main():
    output = Path(os.environ["SPIDERFLY_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.txt").write_text("任务完成\n", encoding="utf-8")
    print("任务完成，结果已保存到 result.txt")


if __name__ == "__main__":
    main()
