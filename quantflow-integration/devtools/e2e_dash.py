# -*- coding: utf-8 -*-
"""
看板 E2E 走查 + 截图 —— 起夹具服务,headless Chromium 逐标签操作并截图。
用法: python devtools/e2e_dash.py [截图输出目录]
产出: <outdir>/01_overview.png ... + 终端打印断言结果(非零退出=有失败)
需要: pip install playwright pandas;Chromium 可执行文件路径按环境变量
      PW_CHROMIUM(默认 /opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell)
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dash_dev

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_shots")
PORT = 18201
CHROMIUM = os.getenv("PW_CHROMIUM",
                     "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell")


def main():
    os.makedirs(OUT, exist_ok=True)
    srv = dash_dev.serve(PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright
    errors, fails = [], []

    def chk(name, cond, extra=""):
        (fails.append(f"{name}: {extra}") if not cond else None)

    with sync_playwright() as p:
        kw = {}
        if os.path.exists(CHROMIUM):
            kw["executable_path"] = CHROMIUM
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": 1360, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        base = f"http://127.0.0.1:{PORT}"

        def shot(name):
            pg.screenshot(path=os.path.join(OUT, name), full_page=True)

        pg.goto(base + "/dash")
        pg.wait_for_timeout(900)
        chk("总览-总资产", "1,021,300" in pg.inner_text("#k_asset"))
        chk("总览-权益曲线", "polyline" in pg.inner_html("#equity"))
        shot("01_overview.png")

        pg.click("a[data-t=live]"); pg.wait_for_timeout(300)
        chk("实盘-持仓", "沪深300ETF" in pg.inner_text("#l_positions"))
        chk("实盘-模式徽标", "干跑" in pg.inner_text("#l_mode"))
        chk("实盘-账本对照", "97500" in pg.inner_text("#l_ledger"))
        shot("02_live.png")

        pg.click("a[data-t=screen]"); pg.wait_for_timeout(800)
        chk("选股-行数", int(pg.inner_text("#s_count")) >= 10, pg.inner_text("#s_count"))
        chk("自选-列表", "贵州茅台" in pg.inner_text("#w_list"))
        shot("03_screen.png")
        # 添加自选全链路
        pg.fill("#w_code", "512480"); pg.click("#w_add"); pg.wait_for_timeout(600)
        chk("自选-添加", "✅" in pg.inner_text("#w_status"), pg.inner_text("#w_status"))
        chk("自选-添加后入表", "512480" in pg.inner_text("#s_table"))
        # 增量更新全链路(夹具秒级完成)
        pg.click("#w_refresh"); pg.wait_for_timeout(2600)
        chk("更新-完成", "更新完成" in pg.inner_text("#w_status"), pg.inner_text("#w_status"))
        # 排序 + 大图
        pg.click("#s_table th[data-k=vol60]"); pg.wait_for_timeout(200)
        pg.click("#s_table tr.click"); pg.wait_for_timeout(500)
        chk("选股-大图", "polyline" in pg.inner_html("#s_detail"))
        shot("04_screen_detail.png")

        pg.click("a[data-t=news]"); pg.wait_for_timeout(600)
        chk("资讯-列表", "成交额环比放大" in pg.inner_text("#n_list"))
        shot("05_news.png")

        pg.click("a[data-t=ops]"); pg.wait_for_timeout(300)
        chk("决策-表格", "月度再平衡" in pg.inner_text("#o_dec"))
        chk("决策-复盘", "放行买入后涨" in pg.inner_text("#o_dec"))
        shot("06_ops.png")

        pg.click("a[data-t=reports]"); pg.wait_for_timeout(300)
        chk("报告-列表", "review_20260710.md" in pg.inner_text("#r_md"))
        shot("07_reports.png")

        pg.click("a[data-t=system]"); pg.wait_for_timeout(300)
        chk("系统-引擎", "alloc_p1" in pg.inner_text("#y_engine"))
        chk("系统-数据", "20" in pg.inner_text("#y_db"))
        shot("08_system.png")
        b.close()

    print("JS errors:", errors or "none")
    print("FAILS:", fails or "none — ALL PASS")
    print("screenshots ->", OUT)
    sys.exit(1 if (fails or errors) else 0)


if __name__ == "__main__":
    main()
