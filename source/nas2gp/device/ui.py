# ============================================
# FILE: nas2gp/ui.py
# PURPOSE: ADB 経由で Google フォトの UI を取得・解析し、バックアップ状態を判定するヘルパー。
#          Helpers to capture and parse the Google Photos UI via ADB and detect backup status.
# ============================================

import os
import re
import subprocess
import time
import atexit
import xml.etree.ElementTree as ET
from nas2gp.core.i18n import tr

UI_XML = "ui.xml"


def _device_ready_soft():
    """v1.7.5: ADB FAIL 雪崩 防止 用 の 軽量 ヘルパー 。
    戻り値 = True なら device 状態 (has_device(check_offline_too=False) OK)。
    False なら offline / no devices / unauthorized で 、 高コスト な scroll_to_top / prepare_ui / dump_ui
    など を 一切 実行 せ ず に 早期 return する べき 。
    例外 は 全て 内部 で try/except で 潰し 、 安全 側 (False) で 返す 。

    v1.7.5: Lightweight helper to prevent ADB FAIL avalanches. Returns True when the
    device is ready; otherwise returns False after swallowing all exceptions."""
    try:
        from nas2gp.device.adb import ADB as _ADB_SOFT
        return bool(_ADB_SOFT.has_device(check_offline_too=False))
    except Exception:
        return False


def _run(*args):
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def adb(*args, max_retry=2, label=None, print_on_console=True):
    """
    ADBコマンド実行: 失敗したら最大 max_retry 回再試行し、
    成功/失敗を print でコンソール直接表示する（デバッグ容易化）。
    成功なら True、全試行失敗なら False を 返す 。

    v1.7.5 ADB FAIL 雪崩 対策:
      ・まず has_device(check_offline_too=False) だけ 軽量 チェック 。
        device 状態 で なけれ ば (offline / unauthorized / no devices) リトライ 一切 せ ず 、
        最初 の 1 回 だけ 実行 し て 結果 を 返す 。
        → デバイス が 居 ない 状態 で 何十回 も リトライ し て WARNING を 連打 する 雪崩 を 防止 。

    Runs an ADB command with up to max_retry retries and prints success/failure to
    the console. Returns True on success, False if every attempt fails.
    v1.7.5: When the device is not ready, retries are skipped entirely to prevent
    a flood of WARNING spam (ADB FAIL avalanche).
    """
    import subprocess as _sp
    last_err = None
    cmd_str = "adb " + " ".join(str(a) for a in args)
    try:
        from nas2gp.device.adb import ADB as _ADB_UI
        _ready = _ADB_UI.has_device(check_offline_too=False)
    except Exception:
        _ready = True
    if _ready:
        attempts_total = max_retry + 1
    else:
        attempts_total = 1
    for attempt in range(attempts_total):
        try:
            r = _sp.run(
                ["adb", *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20,
            )
            if r.returncode == 0:
                if attempt > 0 and print_on_console and label:
                    ret_label = tr("ui.adb.retry_prefix").format(n=attempt)
                    print("  " + tr("ui.adb.label_format").format(ret=ret_label, label=label, cmd=cmd_str))
                return True
            else:
                last_err = (f"rc={r.returncode}", (r.stderr or "").strip()[:120])
                # v1.7.5: 「no devices/emulators found」 / 「device offline」 等 の
                #   物理 的 な 接続 断絶 を 検知 し た 場合 、 これ 以上 リトライ し て も 無駄 な ので
                #   即座 に for ループ を break し て 結果 を 返す 。
                # v1.7.5: On a physical disconnect (no devices / device offline etc.),
                #   further retries are pointless, so break the loop immediately.
                stderr_low = ((r.stderr or "") + " " + (r.stdout or "")).lower()
                if ("no devices/emulators found" in stderr_low
                        or "device offline" in stderr_low
                        or "device unauthorized" in stderr_low
                        or "protocol fault" in stderr_low):
                    break
        except Exception as e:
            last_err = ("exc", f"{type(e).__name__}: {e}")
        # retry前少し休む (ただし device 居ない 場合は 一回 で break する ので 基本 的 に ここ は 通ら ない)
        # Brief pause before retrying (usually not reached when no device is present).
        if attempt < attempts_total - 1:
            time.sleep(0.5)
    # 全試行失敗
    # All attempts failed.
    if print_on_console and label:
        ret_label = tr("ui.adb.fail_prefix")
        print("  " + tr("ui.adb.label_format").format(ret=ret_label, label=label, cmd=cmd_str) + f" -> {last_err}")
    try:
        from nas2gp.logger import Logger as _Lg
        _Lg.warning(f"adb failed after retries: cmd={cmd_str} err={last_err}")
    except Exception:
        pass
    return False


def key_back():
    adb("shell", "input", "keyevent", "KEYCODE_BACK", label="key_back")


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)), label=f"tap({int(x)},{int(y)})")


def swipe_up(label="swipe_up", y_low=None, y_high=None, duration_ms=300):
    """
    【スクロール方向: 画面上方(ページ先頭)に向かってコンテンツを移動】

    adb input swipe の引数順序:
      adb shell input swipe X1 Y1 X2 Y2 duration
        = 指を (X1,Y1) から (X2,Y2) へ移動させる操作

    画面座標: y=0 が画面上端, y=2400 が画面下端
      y_low  = 数値が大きい = 物理的に画面の下側
      y_high = 数値が小さい = 物理的に画面の上側

    本関数 swipe_up():
      Y1=y_low (大: 下端付近) → Y2=y_high (小: 上端付近)
      = 指を「画面下から上」になぞる操作
      = ページ(コンテンツ)は相対的に「下に移動」する
      = ユーザーが指で下から上へスワイプして「過去(上)の投稿を見る」と同じ動き
      = 結果: 『画面上方(ページ先頭)に向かってスクロールする』 ← これが我々の目的

    デフォルト y_low=1800, y_high=350, duration=300ms

    Scroll dir: move content toward the top of the page. Swipes the finger from
    the bottom (y_low) to the top (y_high). Defaults: 1800 -> 350.
    """
    if y_low is None: y_low = 1800
    if y_high is None: y_high = 350
    try:
        from nas2gp.core.settings import get_effective_scroll_log_level
        _LV = get_effective_scroll_log_level()
    except Exception:
        _LV = 1
    # LOG_VERBOSITY=minimal (level=-1) の 時 は 方向 print 無し かつ label も 短縮 極小
    # In minimal mode: no direction print and a very short label.
    if _LV < 0:
        adb_label = label
    else:
        direction_print_ok = _LV >= 2   # 方向付きラベルは VERBOSE のみ / direction labels only in VERBOSE
        if direction_print_ok:
            direction = tr("log.ui.swipe_up_dir").format(y_low=y_low, y_high=y_high)
            adb_label = f"{label}(UP:{direction})"
        else:
            adb_label = f"{label}(UP)"
    ok = adb(
        "shell", "input", "swipe", "540", str(int(y_low)),
        "540", str(int(y_high)), str(int(duration_ms)),
        label=adb_label,
    )
    time.sleep(0.9)
    return ok


def swipe_down(label="swipe_down", y_high=None, y_low=None, duration_ms=300):
    """
    【スクロール方向: 画面下方(ページ末尾)に向かってコンテンツを移動】

    swipe_down():
      Y1=y_high (小: 上端付近) → Y2=y_low (大: 下端付近)
      = 指を「画面上から下」になぞる操作
      = ページ(コンテンツ)は相対的に「上に移動」する
      = ユーザーが指で上から下へスワイプして「新着(下)の投稿を見る」と同じ動き
      = 結果: 『画面下方(ページ末尾)に向かってスクロールする』

    Scroll dir: move content toward the bottom of the page. Swipes the finger
    from the top (y_high) to the bottom (y_low) of the screen.
    """
    if y_high is None: y_high = 400
    if y_low is None: y_low = 1700
    try:
        from nas2gp.core.settings import get_effective_scroll_log_level
        _LV2 = get_effective_scroll_log_level()
    except Exception:
        _LV2 = 1
    # LOG_VERBOSITY=minimal (level=-1) の 時 は label そのまま 短縮
    # In minimal mode the label is kept short.
    if _LV2 < 0:
        adb_label = label
    else:
        direction_print_ok = _LV2 >= 2   # VERBOSE のみ長文方向ラベル
        # Long direction labels are shown only in VERBOSE mode.
        if direction_print_ok:
            direction = tr("log.ui.swipe_down_dir").format(y_high=y_high, y_low=y_low)
            adb_label = f"{label}(DOWN:{direction})"
        else:
            adb_label = f"{label}(DOWN)"
    ok = adb(
        "shell", "input", "swipe", "540", str(int(y_high)),
        "540", str(int(y_low)), str(int(duration_ms)),
        label=adb_label,
    )
    time.sleep(0.9)
    return ok


def wakeup():
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", label="wakeup")


def screen_off():
    """
    画面を OFF (スリープ) にする。
    強制終了時 (Ctrl+C / 例外 / 正常終了) の atexit ハンドラからも呼ばれる。
    ※ adb() の return False は無視: デバイス未接続 / 既に OFF でも問題なし

    Turns the screen off (sleep). Also called from the atexit handler; ignores
    adb failures since an unconnected / already-off device is fine.
    """
    try:
        print()
        print("  " + tr("ui.screenoff.phrase"))
        adb(
            "shell", "input", "keyevent", "KEYCODE_SLEEP",
            label="screen_off(atexit/ctrl+c/exception)",
            max_retry=1,
        )
        # 念のため2回送信 (一部機種で稀に1回目が失敗する場合対策)
        # Send a second time just in case (some models rarely miss the first).
        time.sleep(0.3)
        adb(
            "shell", "input", "keyevent", "KEYCODE_SLEEP",
            label="screen_off(confirm_2nd)",
            max_retry=0,
        )
        print("  " + tr("ui.screenoff.ok"))
    except Exception:
        # 画面OFFに失敗してもメインのクラッシュ情報は表示したいので潰す
        # Swallow errors so the main crash information can still be displayed.
        pass


atexit.register(screen_off)


def unlock():
    # 大きく下から上へスワイプ: ロック解除操作
    # Large bottom-to-top swipe: unlock gesture.
    adb(
        "shell", "input", "swipe", "540", "2000", "540", "400", "260",
        label="unlock_swipe",
    )
    time.sleep(0.7)


def launch_photos():
    adb(
        "shell",
        "monkey",
        "-p",
        "com.google.android.apps.photos",
        "1",
    )
    time.sleep(2.2)


def scroll_to_top(label="scroll_to_top(force)"):
    """
    画面先頭（トップ）へ**確実に**スクロールする。
    バックアップステータス文言は画面上部に表示されるため、取得前に必ずこれを実行する。

    [強力版フロー (合計7段階)]:
      1. print 直接出力開始ログ
      2. KEYCODE_MOVE_HOME ×2  : RecyclerView を先頭に強制移動（最も効く）
      3. 大 swipe_down 1回 (上端250 → 下端1950, 長い距離一気に先頭へ)
         ※ 2026-08-08 ユーザー実機フィードバックによる方向反転:
            通常 swipe_up (Y大->Y小) が逆に動く(上から下に進む)ため、
            scroll_to_top の目的では swipe_down (Y小->Y大) を使用するよう訂正
      4. 中 swipe_down 3回 (上端450 → 下端1600)
      5. 最後に小 swipe_down 1回 (上端350 → 下端1200)
      6. KEYCODE_MOVE_HOME ×1 (最終確認)
      7. print 直接出力終了ログ

    ※ swipe の座標は x=540 (端末の多機種の画面中央より少し右, 解像度 1080/1440両対応)
       y座標は 250〜1950 であれば多くの端末でバー・ナビゲーションに被らない

    Reliably scrolls the screen to the top via a 7-step sequence (MOVE_HOME keys plus
    large/medium/small swipe_down gestures), since the backup status text appears at
    the top of the Photos screen.
    """
    # v1.7.5: デバイス が device 状態 で ない 場合 、 何 十回 も の ADB FAIL を 起こす ので 一切 の 操作 を SKIP
    # v1.7.5: Skip all operations when the device is not ready to avoid repeated ADB failures.
    if not _device_ready_soft():
        return
    try:
        from nas2gp.core.settings import get_effective_scroll_log_level
        _LV3 = get_effective_scroll_log_level()
    except Exception:
        _LV3 = 1
    # LOG_VERBOSITY=minimal (level=-1): scroll_to_top 内 print 一切 無し (label も 短縮)
    # minimal: no prints inside scroll_to_top (label also shortened).
    if _LV3 < 0:
        pass
    elif _LV3 >= 1:
        # Level0 (MINIMAL デフォルト): scroll_to_top内 長文print一切無し
        # Level0 (MINIMAL default): no verbose prints inside scroll_to_top.
        print("  " + tr("ui.scroll.verbose_start").format(label=label))
        if _LV3 >= 2:
            print("    " + tr("ui.scroll.verbose_dir1"))
            print("    " + tr("ui.scroll.verbose_dir2"))
            print("    " + tr("ui.scroll.verbose_dir3"))
    # 2. KEYCODE_MOVE_HOME (RecyclerView 先頭キー) 2回
    # 2. KEYCODE_MOVE_HOME (RecyclerView to top) twice.
    home_label_level = _LV3 >= 2
    short_label = (_LV3 < 0)
    for i in range(2):
        if short_label:
            _hl = f"{label[0:3]}H{i+1}" if label else f"H{i+1}"
        else:
            _hl = (tr("log.ui.home_force_label").format(label=label, idx=i+1) if home_label_level else f"{label}_HOME#{i+1}")
        adb(
            "shell", "input", "keyevent", "KEYCODE_MOVE_HOME",
            label=_hl,
        )
        time.sleep(0.4)
    # 3. 大 swipe_down: 上の方 (250) -> 下の方 (1950)  ← 方向反転適用
    # 3. Large swipe_down: top (250) -> bottom (1950); direction inverted.
    swipe_down(label=f"{label}_BIG", y_high=250, y_low=1950, duration_ms=380)
    # 4. 中 swipe_down 3回 (上端450 -> 下端1600)
    # 4. Medium swipe_down three times (450 -> 1600).
    for i in range(3):
        swipe_down(
            label=f"{label}_MID#{i+1}",
            y_high=450, y_low=1600, duration_ms=300,
        )
    # 5. 小 swipe_down 1回 (上端350 -> 下端1200)
    # 5. One small swipe_down (350 -> 1200).
    swipe_down(label=f"{label}_SMALL", y_high=350, y_low=1200, duration_ms=260)
    # 6. 最終 HOME キー 1回
    # 6. Final HOME key once.
    adb(
        "shell", "input", "keyevent", "KEYCODE_MOVE_HOME",
        label=(tr("log.ui.home_final_label").format(label=label) if home_label_level else f"{label}_HOME#final"),
    )
    time.sleep(0.7)
    if _LV3 >= 2:
        print("    " + tr("ui.scroll.verbose_dir4"))
    if _LV3 >= 1:
        print("  " + tr("ui.scroll.verbose_done").format(label=label))


def prepare_ui():
    """
    GoogleフォトのUI取得ができる状態まで持っていく

    Brings the device into a state where the Google Photos UI can be captured.
    """
    # v1.7.5 ADB FAIL 雪崩 防止: デバイス 不在 なら wakeup など 一切 行わ ず 早期 return
    # v1.7.5 prevent ADB failure avalanches: return early if no device is present.
    if not _device_ready_soft():
        return

    wakeup()
    unlock()
    launch_photos()
    scroll_to_top()


def try_close_tutorial():
    """
    tutorial状態からの脱出を試みる（多段階 Escape シーケンス）。
      1. BACK x 2
      2. 「試してみる」ボタン周辺を5箇所タップ（画面解像度自動検知）
      3. 上スワイプ x 2 → 各後にBACK
      4. まだ tutorial なら 最終手段: force-stop → prepare_ui() でアプリ再起動

    Attempts to escape the tutorial screen via a multi-step sequence: BACK taps,
    area taps around the button, swipes, then force-restart the Photos app.
    """
    from nas2gp.logger import Logger

    Logger.warning("Tutorial detected: try escape sequence")

    for _ in range(2):
        key_back()

    try:
        size_stdout = (_run("shell", "wm", "size").stdout or "").strip()
        m = re.search(r"(\d+)x(\d+)", size_stdout)
        if m:
            w = int(m.group(1))
            h = int(m.group(2))
        else:
            w, h = 1080, 2200
    except Exception:
        w, h = 1080, 2200

    cx = w // 2
    cy = int(h * 0.82)
    tap_points = [
        (cx, cy),
        (cx, cy - 120),
        (cx, cy + 80),
        (cx - 140, cy),
        (cx + 140, cy),
        (cx, int(h * 0.90)),
    ]
    for (dx, dy) in tap_points:
        tap(dx, dy)

    for _ in range(2):
        swipe_up()
        key_back()

    status = _get_status_without_prepare()
    if status == "tutorial":
        Logger.warning("Tutorial still detected: force-restart Photos app")
        try:
            _run(
                "shell",
                "am",
                "force-stop",
                "com.google.android.apps.photos",
            )
            time.sleep(2)
        except Exception:
            pass
        prepare_ui()
    else:
        Logger.info(f"Tutorial escaped, current status = {status}")


def dump_ui(max_retry=3):
    """
    uiautomator dump を実行して /sdcard/ui.xml を pull する。
    空XML / パース失敗 / タイムアウトの疑いがあれば max_retry 回まで再試行する。

    v1.7.5 ADB FAIL 雪崩 対策:
      先頭 で _device_ready_soft() チェック を 行い 、 device 状態 で なけれ ば
      1 回 も 実行 せ ず に 早期 return (WARNING も 出さ ない 。 デバイス 居 ない のは 既知 の ため)。

    Runs uiautomator dump and pulls /sdcard/ui.xml, retrying up to max_retry times
    on empty XML / parse failure / timeout.
    v1.7.5: Returns early without running at all (and without warnings) when the
    device is not ready.
    """
    if not _device_ready_soft():
        return
    last_ok = False
    last_err = None
    for attempt in range(max_retry):
        try:
            r1 = _run("shell", "uiautomator", "dump", "--compressed", "/sdcard/ui.xml")
            if r1.returncode != 0:
                # 旧機種は --compressed 非対応の可能性があるので fallback
                # Fallback for older models that may not support --compressed.
                _run("shell", "uiautomator", "dump", "/sdcard/ui.xml")
            if os.path.exists(UI_XML):
                try:
                    os.remove(UI_XML)
                except OSError:
                    pass
            _run("pull", "/sdcard/ui.xml", UI_XML)
            if os.path.exists(UI_XML) and os.path.getsize(UI_XML) >= 512:
                last_ok = True
                break
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    if not last_ok:
        from nas2gp.logger import Logger as _Lg
        if last_err:
            _Lg.warning(f"dump_ui failed after {max_retry} tries: {last_err}")
        else:
            _Lg.warning(f"dump_ui produced too-small / empty xml after {max_retry} tries")


def _persist_ui_xml_for_debug(prefix="unknown"):
    """
    調査用: 現在の ui.xml を logs/ui_dump_*.xml に日付つきで保存 (最大20世代)

    Debug aid: saves the current ui.xml to logs/ui_dump_*.xml with a timestamp
    (keeping up to 20 generations).
    """
    try:
        import shutil
        from datetime import datetime
        if not os.path.exists(UI_XML):
            return
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(log_dir, f"ui_dump_{prefix}_{ts}.xml")
        shutil.copy2(UI_XML, dst)

        try:
            files = sorted(
                [
                    os.path.join(log_dir, n)
                    for n in os.listdir(log_dir)
                    if n.startswith("ui_dump_") and n.endswith(".xml")
                ]
            )
            if len(files) > 20:
                for old in files[:-20]:
                    try:
                        os.remove(old)
                    except OSError:
                        pass
        except Exception:
            pass
    except Exception:
        pass


def read_texts():
    try:
        tree = ET.parse(UI_XML)
        root = tree.getroot()

        texts = []

        for node in root.iter():

            text = node.attrib.get("text", "").strip()
            if text:
                texts.append(text)

            desc = node.attrib.get("content-desc", "").strip()
            if desc:
                texts.append(desc)

        return texts

    except Exception:
        return []


def debug_print():
    prepare_ui()
    dump_ui()

    print(tr("log.ui.text_list_header"))

    for t in read_texts():
        print(t)


def is_lock_screen(texts):

    keywords = [
        "USBデバッグ",
        "充電完了",
        "Android システム",
        "サイレント モード",
    ]

    hit = 0

    for k in keywords:
        if any(k in t for t in texts):
            hit += 1

    return hit >= 2


def is_tutorial(texts):

    keywords = [
        "新たな検索",
        "検索＆クリエイト",
        "試してみる",
    ]

    return any(
        any(k in t for k in keywords)
        for t in texts
    )


def is_google_account_admin_page(texts):
    """
    Googleアカウント管理画面を明示的に検出する。
    （「バックアップ」等の単純キーワードがこの画面にも大量に出現するため）

    Explicitly detects the Google account admin screen, since generic keywords
    like "backup" also appear heavily there.
    """
    admin_keywords = [
        "アカウントの管理",
        "Google アカウント",
        "アカウントにログイン",
        "アカウントを選択",
        "アカウントを追加",
        "パスワードを確認",
        "プライバシーと利用規約",
        "アカウント設定",
    ]
    count = sum(1 for k in admin_keywords if any(k in t for t in texts))
    return count >= 1


def is_photos_home_screen(texts):
    """
    フォトアプリのホーム画面（通知欄）など、
    「バックアップ完了」という文字が静的に表示されてしまう画面を検出する。

    Detects the Photos home screen, where "backup complete" text can appear
    statically and cause false positives.
    """
    home_keywords = [
        "フォト",
        "アルバム",
        "共有",
        "ライブラリ",
        "検索",
    ]
    photo_app_signals = [
        "フォトの設定",
        "バックアップと同期",
        "バックアップの設定",
        "グーグル フォト",
        "Google フォト",
    ]

    in_photo_app = any(
        any(k in t for k in photo_app_signals) for t in texts
    )

    if not in_photo_app:
        return False

    bottom_tabs = sum(1 for k in home_keywords if any(k in t for t in texts))
    return bottom_tabs >= 2


def _screen_context_ok(texts, for_status):
    """
    バックアップステータス判定を行うのに適切な画面コンテキストか確認する。

    条件（AND）:
      1. ロック画面ではない
      2. チュートリアル画面ではない
      3. Googleアカウント管理画面ではない
      4. フォトアプリ内である（底辺タブまたは設定キーワードの検出）

    Checks whether the screen context is suitable for backup-status detection
    (AND of: not lock screen / not tutorial / not account-admin / inside Photos app).
    """
    if for_status in ("lock_screen", "tutorial"):
        return True

    if is_lock_screen(texts):
        return False
    if is_tutorial(texts):
        return False
    if is_google_account_admin_page(texts):
        return False

    if not is_photos_home_screen(texts):
        photo_only_signals = [
            "バックアップ中",
            "バックアップ完了",
            "バックアップが完了",
            "バックアップが完了しました",
            "バックアップは完了",
            "バックアップは完了しました",
            "バックアップ完了済み",
            "バックアップしています",
            "バックアップは最新です",
            "最新のバックアップ",
            "すべてのアイテムがバックアップ",
            "すべてのアイテムがバックアップされました",
            "すべてバックアップ済み",
            "アイテムはすべてバックアップ",
            "枚の写真をバックアップしました",
            "枚の写真をバックアップしています",
            "枚の写真をバックアップ中",
            "アップロード中",
            "アップロード完了",
            "アップロードが完了",
            "アップロードが完了しました",
            "アップロードを待機",
            "Wi-Fi を待機",
            "充電を待機",
            "フォトの設定",
            "バックアップと同期",
            "グーグル フォト",
            "Google フォト",
            "バックアップの詳細",
            "写真の設定",
            "ストレージ管理",
            "アプリとデータの管理",
            # English
            "Backing up",
            "Backup complete",
            "Backup is complete",
            "Backup is up to date",
            "All photos backed up",
            "All items backed up",
            "Upload complete",
        ]
        hit = sum(1 for k in photo_only_signals if any(k in t for t in texts))
        if hit == 0:
            return False

    return True


def is_complete(texts):
    if not _screen_context_ok(texts, "complete"):
        return False

    keywords = [
        "バックアップ完了",
        "バックアップが完了",
        "バックアップが完了しました",
        "バックアップは完了",
        "バックアップは完了しました",
        "バックアップ完了済み",
        "バックアップは最新です",
        "最新のバックアップ",
        "バックアップは最新",
        "すべてのアイテムがバックアップ",
        "すべてのアイテムがバックアップされました",
        "すべてバックアップ済み",
        "アイテムはすべてバックアップ",
        "枚の写真をバックアップしました",
        "同期が完了",
        "同期が完了しました",
        "アップロード完了",
        "アップロードが完了",
        "アップロードが完了しました",
        # English
        "Backing up is complete",
        "Backup is complete",
        "Backup complete",
        "Backup is up to date",
        "Backup up to date",
        "All (photos|items|pictures) backed up",
        "All photos backed up",
        "All items backed up",
        "Upload complete",
    ]

    def _match(t: str) -> bool:
        t_n = str(t or "").strip()
        if not t_n:
            return False
        import re as _ri
        for kw in keywords:
            try:
                if "|" in kw and (kw.startswith("All ") or kw.startswith("all ")):
                    if _ri.search(kw, t_n, _ri.I):
                        return True
                else:
                    if kw.lower() in t_n.lower():
                        return True
            except Exception:
                continue
        return False

    return any(_match(t) for t in texts)


def is_backing_up(texts):
    if not _screen_context_ok(texts, "backing_up"):
        return False

    keywords = [
        "バックアップ中",
        "バックアップしています",
        "アップロード中",
        "アップロードしています",
        "残り",
        "あと",
        "枚の写真をバックアップしています",
        # English
        "Backing up",
        "Uploading",
        "remaining",
        "left",
        "photos to back up",
    ]

    return any(
        any(k in t for k in keywords)
        for t in texts
    )


def is_waiting(texts):
    if not _screen_context_ok(texts, "waiting"):
        return False

    keywords = [
        "アップロードを待機しています",
        "Wi-Fi を待機しています",
        "Wi-Fiに接続されるのを待っています",
        "充電を待機しています",
    ]

    return any(
        any(k in t for k in keywords)
        for t in texts
    )


def _classify_detailed_status(texts):
    """
    v1.7.4 新規: get_backup_status 内部 で status と は 別に 「画面から確実に読み取れた、
    ユーザー が 見 て 安心 できる 詳細 な 補足 状態」 を 返す ため の 分類 関数。
    backup_waiter 内 の bw.status.header の status_label=... 部分 で 利用 さ れ る。

    戻り値: str (None の 場 合 は 従来通り unknown_rate 表示 を そのまま 使う)

    v1.7.4: Classifies a human-readable supplementary state ("scanning", "uploading"
    etc.) read from the screen, returned separately from status. Returns str or None
    (None falls back to the usual unknown_rate display).
    """
    if not texts:
        return None
    normalized = "\n".join(texts)
    up = normalized.upper()

    def _hit(*keywords):
        return any(k in normalized or k in up for k in keywords)

    if _hit("フォトライブラリをスキャン", "フォトライブラリ を スキャン",
            "ライブラリをスキャン", "ライブラリ を スキャン",
            "Scanning media items", "Scanning your library", "Scanning",
            "スキャンしています", "スキャン しています", "スキャン中",
            "メディア アイテム を スキャン", "メディアアイテムをスキャン"):
        return "scan_photo_library"

    if _hit("準備しています", "準備 しています", "準備 中", "準備中",
            "バックアップ の 準備", "バックアップの準備",
            "アップロード の 準備", "アップロードの準備",
            "Preparing backup", "Preparing upload", "Preparing"):
        return "preparing"

    if _hit("チェック 中", "チェック中", "確認 中", "確認中", "Checking",
            "整合性 を 確認", "整合性を確認",
            "写真 を 確認", "写真を確認", "Checking for new photos",
            "重複 を 確認", "重複を確認"):
        return "checking"

    if _hit("Processing", "Processing photos", "Processing videos",
            "処理中", "処理しています", "写真 を 処理", "写真を処理",
            "動画 を 処理", "動画を処理"):
        return "processing"

    if _hit("一時停止", "一時停止 中", "一時停止中", "Paused", "Pause"):
        return "paused"

    if _hit("バックアップ 完了", "バックアップ完了", "バックアップが完了",
            "バックアップは完了", "バックアップ は 最新", "バックアップは最新",
            "Backing up is complete", "Backup complete", "Backup is up to date"):
        return "complete_explicit"

    if _hit("アップロード 中", "アップロード中", "アップロードしています",
            "バックアップ 中", "バックアップ中",
            "Uploading", "Now uploading", "Backing up"):
        return "uploading_explicit"

    return None


def extract_google_photos_progress(texts=None):
    """
    v1.7.5 新規: Google Photos の バックアップ 画面 UI に 表示 さ れる
    「残り X 枚 の 写真 を バックアップ し て い ます」「残り Y 分」 など を
    正規 表現 で 抽出 する 。

    texts=None の 場合 は read_texts() を 呼ん で 最新 の UI dump を 取得 する。
    戻り値 は dict:
      {
        "items_total": int or None,      # 枚数 表示 (例: 27 枚 の 写真 ...)
        "items_done": int or None,       # 現状 は 使用 し ない
        "eta_seconds": int or None,      # 「残り 8 分」 → 480 秒
        "eta_text": str or None,         # 生 の 残り 時間 文言
        "detail_text": str or None,      # 説明 の 1 行 目 を そのまま (バックアップ 中 の 説明)
      }

    v1.7.5 誤抽出 防止 (ユーザー 実機 報告):
      画面 の どこか 別 の 箇所 (ゴミ箱 / ストレージ 管理 / 設定) に
      「残り 823 枚」 など の 文言 が 静的 に 表示 さ れ て いる ケース に 対応 。
      → まず 「完了 形 の 文言」 を 優先 的 に スキャン し 、 1 つ でも ヒット し たら
         items_total / eta 系 を 強制的に None に クリア し 、 「残り X 枚 / 残り Y 分」 の
         誤抽出 を 防止 する 。

    v1.7.5: Extracts "X photos remaining" / "Y minutes left" etc. from the Google
    Photos backup screen via regex. Returns a dict with items_total / eta_seconds /
    eta_text / detail_text. To avoid mis-detection, completion wording is checked
    first, and if found the remaining-count/ETA values are force-cleared to None.
    """
    if texts is None:
        try:
            texts = read_texts() or []
        except Exception:
            texts = []
    result = {
        "items_total": None,
        "items_done": None,
        "eta_seconds": None,
        "eta_text": None,
        "detail_text": None,
    }
    if not texts:
        return result
    import re as _re
    full = "\n".join(texts)
    lines = [ln.strip() for ln in full.splitlines() if ln.strip()]

    # ========== v1.7.5 NEW: 完了 形 の 文言 を 最優先 チェック ==========
    #   「完了 形」 が 1 つ でも あれ ば 、 「残り X 枚 / 残り Y 分」 は すべて 誤抽出 とみなしクリア 。
    # ========== v1.7.5 NEW: check completion wording first ==========
    #   If any completion phrase is present, clear all "X remaining" extractions
    #   as mis-detections.
    complete_patterns = [
        _re.compile(r"バックアップ.*完了", _re.I),
        _re.compile(r"バックアップ.*最新", _re.I),
        _re.compile(r"すべて.*(?:バックアップ|アップロード)", _re.I),
        _re.compile(r"アイテム.*すべて.*バックアップ", _re.I),
        _re.compile(r"アップロード.*完了", _re.I),
        _re.compile(r"同期.*完了", _re.I),
        # English
        _re.compile(r"Backing up\s+is\s+complete", _re.I),
        _re.compile(r"Backup\s+is\s+complete", _re.I),
        _re.compile(r"Backup\s+is\s+up\s+to\s+date", _re.I),
        _re.compile(r"All\s+(items|photos|videos)\s+backed\s+up", _re.I),
        _re.compile(r"\bUpload\s+complete\b", _re.I),
    ]
    is_complete_screen = False
    for p in complete_patterns:
        if p.search(full):
            is_complete_screen = True
            break
    if not is_complete_screen:
        # 念の為 lines 1 行 ずつ 完全 一致 寄り で 再 確認
        # Re-check line by line with near-exact matching just in case.
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            for kw in ("バックアップ完了", "バックアップは最新", "バックアップが完了",
                       "バックアップは完了", "すべてバックアップ済み", "すべてのアイテムがバックアップ",
                       "アップロード完了", "アップロードが完了",
                       "Backing up is complete", "Backup is complete", "Backup is up to date"):
                if kw in s:
                    is_complete_screen = True
                    break
            if is_complete_screen:
                break

    # -------- 枚数 抽出 (★完了 画面 で は items_total を 一切 入れ ない) --------
    # -------- Item count extraction (skip entirely on a complete screen) --------
    if not is_complete_screen:
        patterns_items = [
            _re.compile(r"(?P<n>[0-9]+)\s*枚\s*の\s*写真\s*を\s*バックアップ\s*(?:しています|中)", _re.I),
            _re.compile(r"(?P<n>[0-9]+)\s*枚\s*を\s*アップロード\s*(?:しています|中)", _re.I),
            _re.compile(r"(?P<n>[0-9]+)\s*枚\s*の\s*写真\s*を\s*アップロード\s*(?:しています|中)", _re.I),
            _re.compile(r"Backing up (?P<n>[0-9]+) photos?", _re.I),
            _re.compile(r"(?P<n>[0-9]+) photos? to back up", _re.I),
            _re.compile(r"(?P<n>[0-9]+) items? (?:remaining|left)", _re.I),
            _re.compile(r"残り\s*(?P<n>[0-9]+)\s*枚", _re.I),
        ]
        for p in patterns_items:
            m = p.search(full)
            if m:
                try:
                    result["items_total"] = int(m.group("n"))
                    break
                except Exception:
                    pass

    # -------- 残り 時間 抽出 (★完了 画面 で は eta も 一切 入れ ない) --------
    # -------- ETA extraction (skip entirely on a complete screen) --------
    # 日本 語: 「残り 8 分」 / 「残り 1 時間 2 分」 / 「残り 約 1 時間 10 分」 / 「残り 30 秒」
    # 英 語:  "About 8 minutes left" / "1 hour 2 minutes remaining" / "30 seconds left"
    total_seconds = None
    eta_text = None
    if not is_complete_screen:
        try:
            def _look_for(ln):
                nonlocal total_seconds, eta_text
                ln_n = ln.replace("約", " ").replace("\u3000", " ")
                # 1 時間 20 分 形式
                # "1 hour 20 min" form.
                m = _re.search(r"残り\s*(?:約\s*)?(?P<h>[0-9]+)\s*時間\s*(?P<m>[0-9]+)\s*分", ln_n, _re.I)
                if m:
                    total_seconds = (int(m.group("h")) * 3600) + (int(m.group("m")) * 60)
                    eta_text = m.group(0)
                    return True
                # 時間 だけ
                # hours only
                m = _re.search(r"残り\s*(?:約\s*)?(?P<h>[0-9]+)\s*時間", ln_n, _re.I)
                if m:
                    total_seconds = int(m.group("h")) * 3600
                    eta_text = m.group(0)
                    return True
                # 分 だけ
                # minutes only
                m = _re.search(r"残り\s*(?:約\s*)?(?P<m>[0-9]+)\s*分", ln_n, _re.I)
                if m:
                    total_seconds = int(m.group("m")) * 60
                    eta_text = m.group(0)
                    return True
                # 秒 だけ
                # seconds only
                m = _re.search(r"残り\s*(?:約\s*)?(?P<s>[0-9]+)\s*秒", ln_n, _re.I)
                if m:
                    total_seconds = int(m.group("s"))
                    eta_text = m.group(0)
                    return True
                # English "About 8 minutes left" / "1 hour 25 minutes remaining"
                low = ln.lower()
                m = _re.search(r"(?:about\s+)?(?P<h>[0-9]+)\s*hours?\s+(?P<m>[0-9]+)\s*(?:minutes?|mins?)\s*(left|remaining)", low)
                if m:
                    total_seconds = (int(m.group("h")) * 3600) + (int(m.group("m")) * 60)
                    eta_text = m.group(0)
                    return True
                m = _re.search(r"(?:about\s+)?(?P<m>[0-9]+)\s*(?:minutes?|mins?)\s*(left|remaining)", low)
                if m:
                    total_seconds = int(m.group("m")) * 60
                    eta_text = m.group(0)
                    return True
                m = _re.search(r"(?:about\s+)?(?P<h>[0-9]+)\s*hours?\s*(left|remaining)", low)
                if m:
                    total_seconds = int(m.group("h")) * 3600
                    eta_text = m.group(0)
                    return True
                m = _re.search(r"(?:about\s+)?(?P<s>[0-9]+)\s*(?:seconds?|secs?)\s*(left|remaining)", low)
                if m:
                    total_seconds = int(m.group("s"))
                    eta_text = m.group(0)
                    return True
                return False

            for ln in lines:
                if _look_for(ln):
                    break
        except Exception:
            total_seconds = None
            eta_text = None
    if total_seconds is not None:
        result["eta_seconds"] = int(total_seconds)
    if eta_text:
        result["eta_text"] = eta_text

    # detail_text: 先頭 の 重要 行 を 1 行 だけ (backup / upload の 説明 行)
    # detail_text: only the first few important lines (backup / upload description).
    try:
        import re as _re2
        interesting = []
        pats_detail = [
            _re2.compile(r".*(?:バックアップ|アップロード|Uploading|Backing up|Preparing|Scanning).*", _re2.I),
        ]
        # v1.7.15: 端末 UI の 定型 ヒント (進捗 と は 無関係 な ノイズ) を 除外 。
        # v1.7.15: Exclude fixed UI hints unrelated to progress (noise).
        _hint_pat = _re2.compile(
            r"(アプリを開い|アプリ を 開い|早く進み|Keep the app open|Back(?:ing)? up faster)",
            _re2.I,
        )
        seen = set()
        for ln in lines:
            s = ln.strip()
            if not s or len(s) < 4:
                continue
            if _hint_pat.search(s):
                continue
            if s in seen:
                continue
            for p in pats_detail:
                if p.match(s):
                    interesting.append(s)
                    seen.add(s)
                    break
            if len(interesting) >= 2:
                break
        if interesting:
            result["detail_text"] = " | ".join(interesting[:2])
    except Exception:
        pass
    return result


_last_prepare_ui_ts = 0.0
_PREPARE_UI_CACHE_SECONDS = 45.0

_last_scroll_to_top_ts = 0.0
_SCROLL_TO_TOP_MIN_INTERVAL = 4.0   # 最小4秒間隔（8s → 4s に短縮）
# Minimum 4-second interval (shortened from 8s to 4s)

# バックアップ監視開始時刻: get_backup_status 初回呼び出しで自動設定
# 開始から 60 秒間は MIN_INTERVAL を無視して常に scroll_to_top を実行する
# Backup-monitoring start time: auto-set on the first get_backup_status call.
# For 60s after start, ignore MIN_INTERVAL and always run scroll_to_top.
_monitor_initialized_at_ts = None
_MONITOR_INITIAL_FORCE_SCROLL_SECONDS = 60.0


def _mark_prepare_ui_done():
    global _last_prepare_ui_ts, _last_scroll_to_top_ts
    _last_prepare_ui_ts = time.time()
    _last_scroll_to_top_ts = time.time()


def _prepare_ui_cached_is_fresh():
    return (time.time() - _last_prepare_ui_ts) < _PREPARE_UI_CACHE_SECONDS


def _maybe_scroll_to_top(force=False, label=None):
    """
    最小間隔を守りつつ scroll_to_top() を実行する。
    label が渡されたらログ出力用に Logger 経由 + print 直接コンソール出力 (ユーザー目視用)

    追加ルール:
      - 監視開始から 60 秒間 (_MONITOR_INITIAL_FORCE_SCROLL_SECONDS) は
        MIN_INTERVAL / force フラグに関わらず**毎回強制実行**

    Runs scroll_to_top() while respecting the minimum interval. During the first
    60s after monitoring starts it always runs regardless of force / interval.
    """
    global _last_scroll_to_top_ts, _monitor_initialized_at_ts

    now = time.time()

    # 初回の監視開始時刻を設定 (get_backup_status を最初に呼び出した時刻)
    # Set the first monitoring start time (when get_backup_status was first called).
    if _monitor_initialized_at_ts is None:
        _monitor_initialized_at_ts = now

    initial_phase = (now - _monitor_initialized_at_ts) < _MONITOR_INITIAL_FORCE_SCROLL_SECONDS

    # 「初期フェーズ」 or 「force=True」 or 「最小経過時間以上」 → 実行
    # Run when in the initial phase, or force=True, or the minimum interval elapsed.
    interval_ok = (now - _last_scroll_to_top_ts) >= _SCROLL_TO_TOP_MIN_INTERVAL
    do_scroll = initial_phase or force or interval_ok

    if not do_scroll:
        # 実行しなかった場合もデバッグ的にprint（ただし10秒に1回程度）
        # Even when not executed, a debug print is intended (but only about once per 10s).
        if (now - _last_scroll_to_top_ts) < 2.0:
            # 前回のスクロールから2秒以内はSKIPしてprintもしない（画面うるさい）
            # Skip and print nothing if within 2s of the previous scroll (too noisy).
            pass
        return False

    # NEW v1.7.5 LOG_VERBOSITY 対応 (get_effective_scroll_log_level)
    # NEW v1.7.5: LOG_VERBOSITY support via get_effective_scroll_log_level().
    try:
        from nas2gp.core.settings import get_effective_scroll_log_level
        _LV4 = get_effective_scroll_log_level()
    except Exception:
        _LV4 = 1

    # LOG_VERBOSITY=minimal/standard (level<=0): 「Scroll TOP: xxx」 print/Logger 一切 非表示。
    #   scroll_to_top 実行 自体 は 行う (GP 最上部 へ 戻す 機能 は 維持)
    # minimal/standard (level<=0): suppress all "Scroll TOP" print/Logger output,
    #   but still run scroll_to_top (keep returning GP to the top).
    if _LV4 <= 0:
        scroll_to_top(label=(label if label else "s"))
        _last_scroll_to_top_ts = now
        return True

    # Logger.info の scroll_to_top INFO行: level>=1 のみ出力
    # Logger.info scroll_to_top line: printed only at level>=1.
    if _LV4 >= 1:
        try:
            from nas2gp.logger import Logger as _Lg2
            msg = (
                f"scroll_to_top() execute: label={label}, "
                f"initial_phase={initial_phase}, "
                f"force={force}, "
                f"last_scroll={now - _last_scroll_to_top_ts:.1f}s ago"
            )
            _Lg2.info(msg)
        except Exception:
            pass

    scroll_label = label if label else "scroll_to_top"
    phase_mark = "[INIT]" if initial_phase else ("[FORCE]" if force else "")
    # NORMAL(1) / VERBOSE(2) 以上 で 詳細 print 表示 (standard 以下 は 上で early return 済み)
    # Detailed print at NORMAL(1) / VERBOSE(2)+ (standard and below returned earlier).
    print("  " + tr("ui.scroll.maybe_header").format(
        mark=phase_mark, lbl=scroll_label, sec=now - _last_scroll_to_top_ts,
    ))
    scroll_to_top(label=scroll_label)
    _last_scroll_to_top_ts = now
    return True


def _ensure_screen_on_and_unlocked(texts):
    """
    dump_ui -> read_texts の texts を受け取り、
    ロック画面検知時は wakeup + swipe unlock を1回実行して再dumpする。
    （wakeup/unlock は prepare_ui() より遥かに軽いため、監視中はこちらを優先）

    Takes the texts from dump_ui -> read_texts; on lock-screen detection, performs
    wakeup + swipe unlock once and re-dumps (much lighter than prepare_ui()).
    """
    if is_lock_screen(texts):
        wakeup()
        unlock()
        time.sleep(1.5)
        dump_ui()
        return read_texts()
    return texts


def _get_status_without_prepare():
    """
    try_close_tutorial 内など、prepare_ui() を呼ばずに状態だけ取得したい場合に使う。
    (画面ロック等、起動したて前提ではないケース向け)

    【重要】原則として dump_ui() の直前に scroll_to_top() を実行し、
    画面上部に表示されるバックアップステータス文言を確実にXMLに含める。
    (最小 8 秒間隔制限で過負荷防止)

    Gets only the status without calling prepare_ui() (e.g. inside
    try_close_tutorial). As a rule it runs scroll_to_top() right before dump_ui()
    so the backup status text at the top is included in the XML (rate-limited).
    """
    from nas2gp.logger import Logger as _Lg

    # ★必ず先頭へスクロールしてから dump★
    # ★Always scroll to the top before dumping.★
    _maybe_scroll_to_top(force=False, label="before_dump")
    dump_ui()
    texts = read_texts()
    if not texts:
        # 空の場合: 念のため scroll_to_top(force) + 再 dump を 1 回施行
        # If empty: run scroll_to_top(force) + re-dump once just in case.
        _maybe_scroll_to_top(force=True, label="retry_empty_texts")
        time.sleep(1.0)
        dump_ui()
        texts = read_texts()
        if texts:
            _Lg.info("retry dump + force scroll_to_top recovered empty texts OK")
    if not texts:
        _persist_ui_xml_for_debug(prefix="empty")
    if is_lock_screen(texts):
        return "lock_screen"
    if is_tutorial(texts):
        return "tutorial"
    if is_waiting(texts):
        return "waiting"
    if is_complete(texts):
        return "complete"
    if is_backing_up(texts):
        return "backing_up"
    if not texts:
        return "unknown"
    return "unknown"


_last_get_backup_status_result = None
_unknown_streak = 0
_last_detailed_status_result = None


def get_backup_status():
    """
    バックアップ監視中のステータス取得。
    原則: 軽量 (wakeup + unlock only) + 『必ず scroll_to_top (最小間隔8秒) → dump_ui』
    例外:
      - 前回の完全 prepare_ui から 45秒以上経過 → 完全 prepare_ui() を 1 回実行
      - lock_screen 検出 → wakeup + unlock して再取得
      - texts が空 (dump失敗) や tutorial → 即座に prepare_ui() をフル実行
      - unknown 連続 2回以上 → force=True で scroll_to_top して prepare_ui() fallback

    v1.7.4 追加: 戻り値 は str status の まま 互換性 維持。詳細 補足 状態 は
                 `_last_detailed_status_result` (module global) に 保存 し、
                 backup_waiter.py 側 から `get_last_detailed_status()` で 取得 可能 と する。

    v1.7.5 ADB FAIL 雪崩 対策: 先頭 で _device_ready_soft() を チェック し 、
      device 状態 で ない 場合 、 一切 の ADB 通信 を SKIP し て 即座 に
      「unknown (DEVICE_NOT_READY)」 を 返す 。 これ に より 数十 WARNING の 雪崩 を 根本 的 に 断つ 。

    Gets the backup status during monitoring: lightweight (wakeup + unlock only) plus
    "always scroll_to_top -> dump_ui", with fallbacks (periodic full prepare_ui,
    lock-screen re-fetch, empty/tutorial full prepare_ui, unknown-streak recovery).
    v1.7.4: Return value stays a str status for compatibility; the detailed
    supplementary state is stored in the module global `_last_detailed_status_result`
    and can be read via `get_last_detailed_status()`.
    v1.7.5: Checks _device_ready_soft() first and, when the device is not ready,
    skips all ADB traffic and immediately returns "unknown (DEVICE_NOT_READY)".
    """
    global _last_get_backup_status_result, _unknown_streak, _last_detailed_status_result
    from nas2gp.logger import Logger as _Lg

    if not _device_ready_soft():
        _last_get_backup_status_result = "unknown"
        try:
            _last_detailed_status_result = None
        except Exception:
            pass
        try:
            _unknown_streak += 1
        except Exception:
            pass
        return "unknown"

    full_prepare = (not _prepare_ui_cached_is_fresh())

    if full_prepare:
        prepare_ui()
        _mark_prepare_ui_done()
    else:
        wakeup()
        time.sleep(0.5)
        # ★ 軽量モードでも、毎回の取得直前に scroll_to_top 実施
        # ★ Even in lightweight mode, run scroll_to_top right before each fetch.
        _maybe_scroll_to_top(force=False, label="lightweight_wakeup")
        dump_ui()
        # ロック画面なら解除して再取得
        # If on the lock screen, unlock and re-fetch.
        texts = read_texts()
        texts2 = _ensure_screen_on_and_unlocked(texts)
        if texts2 is texts:
            # ロック解除が走らなかった場合：
            # 空 / tutorial のときだけフル prepare_ui に Fallback
            # If unlock did not run: fall back to full prepare_ui only when empty/tutorial.
            if (not texts2) or is_tutorial(texts2):
                _Lg.info("get_backup_status: empty/tutorial fallback -> full prepare_ui()")
                prepare_ui()
                _mark_prepare_ui_done()

    status = _get_status_without_prepare()

    # v1.7.4 新規: 詳細 状態 分類 (画面 に 「スキャン中」 等 が 出 て い れ ば 補足 情報 と し て 保存)
    # v1.7.4: Classify the detailed state (e.g. "scanning") and store it as supplementary info.
    try:
        _last_detailed_status_result = _classify_detailed_status(read_texts())
    except Exception:
        _last_detailed_status_result = None

    # 念のため: ロックがまだ解除できていなければ wakeup+unlock 再施行して status 再計算
    # Just in case: if still locked, redo wakeup+unlock and recompute the status.
    if status == "lock_screen":
        wakeup(); unlock(); time.sleep(1.5)
        status = _get_status_without_prepare()

    # unknown連続検知 → 1回目から即時 force scroll_to_top + prepare_ui fallback
    # (2回まで待つと unknown の期間が長すぎるため、今回は 1 回発生で直ちに復旧を試みる)
    # On the first unknown, immediately force scroll_to_top + prepare_ui fallback
    # (waiting for two would keep unknown too long, so recover after just one).
    if status == "unknown":
        _unknown_streak += 1
    else:
        _unknown_streak = 0
    if _unknown_streak >= 1:
        _Lg.warning(
            f"get_backup_status: unknown streak={_unknown_streak} -> "
            f"force scroll_to_top + prepare_ui() to recover"
        )
        if _unknown_streak == 1:
            print("  " + tr("ui.scroll.unknown_recovery1").format(screen=_screen_phrase_for_unknown()))
        else:
            print("  " + tr("ui.scroll.unknown_recovery2").format(
                cnt=_unknown_streak, screen=_screen_phrase_for_unknown()))
        _maybe_scroll_to_top(force=True, label=f"unknown_streak_{_unknown_streak}")
        time.sleep(0.8)
        prepare_ui()
        _mark_prepare_ui_done()
        status = _get_status_without_prepare()
        if status == "unknown":
            _persist_ui_xml_for_debug(prefix=f"unk_streak{_unknown_streak}")
    else:
        if status == "unknown":
            try:
                import random as _r
                if _r.random() < 0.33:
                    _persist_ui_xml_for_debug(prefix="unknown")
            except Exception:
                pass

    _last_get_backup_status_result = status
    return status


def get_last_detailed_status():
    """v1.7.4 新規: get_backup_status() が 直前 の 画面 OCR から 分類 し た 詳細 補足 状態 を 返す。
    存在 し ない とき は None。

    v1.7.4: Returns the detailed supplementary state classified from the last screen
    OCR by get_backup_status(); None when absent."""
    return _last_detailed_status_result


def _screen_phrase_for_unknown():
    """v1.11.2 新規: UNKNOWN 検知 時 に 、 画面 から 読み取れ た 文言 (短縮 表現) を 返す 。
    認識 でき なかっ た とき は 「認識 でき ず」 の 定型 文言 を 返す 。

    v1.11.2: Returns a short on-screen phrase for UNKNOWN detection, or a generic
    "could not recognize" message when no state was classified."""
    code = _last_detailed_status_result
    if code:
        try:
            _s = tr(f"ui.screen.{code}")
            if _s and not str(_s).startswith("ui.screen."):
                return str(_s)
        except Exception:
            pass
    return tr("ui.screen.unknown")
