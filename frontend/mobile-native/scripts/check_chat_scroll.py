"""ADB smoke test: opening a long conversation should show the newest message."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from migrate_desktop_to_android import Devtools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    with Devtools(args.adb, args.serial) as devtools:
        def click(expression: str):
            devtools.evaluate(expression)
            time.sleep(0.4)

        def measure(label: str):
            result = devtools.evaluate("""(()=>{
              const node=document.querySelector('.mobile-messages');
              return node ? {top:node.scrollTop,viewport:node.clientHeight,
                total:node.scrollHeight,count:node.querySelectorAll('.mobile-message').length} : null;
            })()""")
            if not result or result["count"] == 0:
                raise RuntimeError(f"{label}: 未找到可验证的聊天消息")
            gap = result["total"] - result["viewport"] - result["top"]
            print(f"{label}: 消息数={result['count']}，距底部={gap:.1f}px")
            if gap > 3:
                raise RuntimeError(f"{label}: 未滚动到最新消息")

        click("document.querySelectorAll('.mobile-nav button')[0].click()")
        measure("打开聊天页")
        devtools.evaluate("document.querySelector('.mobile-messages').scrollTop=0")
        click("document.querySelectorAll('.mobile-nav button')[4].click()")
        click("document.querySelectorAll('.mobile-nav button')[0].click()")
        measure("从模型页返回")
        devtools.evaluate("document.querySelector('.mobile-messages').scrollTop=0")
        click("document.querySelectorAll('.mobile-nav button')[1].click()")
        click("document.querySelector('.mobile-conversation.selected').click()")
        measure("重新打开同一段对话")


if __name__ == "__main__":
    main()
