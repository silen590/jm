#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JM 漫画下载导出 CLI
===================

基于 jmcomic 库的漫画下载导出工具：
1. 输入禁漫车号（jmid，如 350234）即可下载整本漫画到本地（每页一张图片）。
2. 支持把下载下来的图片导出为 PDF 或 APNG 动画（或两者都要）。
3. 内容按 “输出目录/作者/[JM车号]标题/…” 分类存放。

PDF 导出复用 jmcomic 内置的 Img2pdfPlugin（img2pdf 实现）；
APNG 导出参考 Img2pdfPlugin 的实现，编写了自定义插件 Img2ApngPlugin
（Pillow 实现，把全部页面拼成可循环播放的 APNG 动画）。

用法示例：
    python3 jm_export.py 350234                                  # 同时导出 pdf + apng
    python3 jm_export.py 350234 -f apng                          # 只要 apng
    python3 jm_export.py 350234 -f pdf -o ./my_manga             # 只要 pdf，指定输出目录
    python3 jm_export.py 350234 -f apng --cover auto             # 自动加封面首帧
    python3 jm_export.py 350234 -f apng --cover ./cover.jpg      # 用本地图片做首帧
    python3 jm_export.py 350234 -f apng --duration 500           # 每帧 500ms
    python3 jm_export.py 350234 -f apng --duration 800,400,400   # 逐帧时长（首帧800ms）
    python3 jm_export.py 350234 --cover auto --cover-duration 1500 # 封面帧单独停留
    python3 jm_export.py 350234 350235 -o ./my_manga             # 批量下载多个车号
    python3 jm_export.py 350234 --info                           # 只看元信息不下载
    python3 jm_export.py 350234 --photo 350235                   # 只处理某个章节
    python3 jm_export.py 350234 --option ./option.yml            # 使用自定义 jmcomic 配置
    python3 jm_export.py 350234 --delete-original                # 打包后删除原图
"""

import argparse
import os
import sys

import jmcomic
from jmcomic import (
    JmOptionPlugin,
    JmModuleConfig,
    Feature,
    PluginFeature,
    download_album,
    download_photo,
    files_of_dir,
    jm_log,
    DirRule,
)

__version__ = "1.1.0"


def _maybe_scale(image, scale):
    """按比例缩放图片（scale=1.0 或 None 时原样返回）。"""
    from PIL import Image
    if scale in (None, 1.0) or float(scale) <= 0:
        return image
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:  # 兼容旧版 Pillow
        resample = Image.LANCZOS
    new_w = max(1, int(image.width * float(scale)))
    new_h = max(1, int(image.height * float(scale)))
    if (new_w, new_h) != image.size:
        image = image.resize((new_w, new_h), resample)
    return image


def _normalize_duration(duration):
    """
    归一化帧时长：
    - int / 数字字符串 → 统一为 int（所有帧相同）
    - "400,800,1200" → [400, 800, 1200]（逐帧时长）
    - 列表 → 逐帧时长
    """
    if isinstance(duration, (list, tuple)):
        return [max(1, int(d)) for d in duration]
    if isinstance(duration, str) and "," in duration:
        return [max(1, int(x)) for x in duration.split(",") if x.strip() != ""]
    return max(1, int(duration))


# ---------------------------------------------------------------------------
# 自定义插件：把漫画图片合并为一个 APNG 动画
# 实现完全参考 jmcomic 内置的 Img2pdfPlugin（见 jm_plugin.py），
# 只是把“用 img2pdf 合成 PDF”换成“用 Pillow 合成 APNG”。
# ---------------------------------------------------------------------------


class Img2ApngPlugin(JmOptionPlugin):
    plugin_key = "img2apng"

    def invoke(self,
               photo: "JmPhotoDetail" = None,
               album: "JmAlbumDetail" = None,
               downloader=None,
               img_dir=None,
               filename_rule="Pid",
               dir_rule=None,
               delete_original_file=False,
               duration=400,
               loop=0,
               scale=1.0,
               cover_path=None,
               cover_duration=None,
               **kwargs,
               ):
        if photo is None and album is None:
            jm_log("wrong_usage", "img2apng必须运行在after_photo或after_album时")

        try:
            from PIL import Image
        except ImportError:
            self.warning_lib_not_install("PIL")
            return

        self.delete_original_file = delete_original_file

        # 决定生成的 apng 文件路径（与 Img2pdfPlugin 的 decide_filepath 用法一致）
        apng_filepath = self.decide_filepath(
            album, photo, filename_rule, "apng", img_dir, dir_rule
        )

        # 把各章节目录下的所有图片合成为一个 apng
        result = self.write_img_2_apng(
            apng_filepath, album, photo,
            duration=duration, loop=loop, scale=scale,
            cover_path=cover_path, cover_duration=cover_duration,
        )
        if not result:
            return
        img_path_ls, img_dir_ls = result

        detail = album or photo
        if downloader is not None:
            downloader.record_export_filepath(detail, apng_filepath)

        self.log(
            f"{detail.alias_cn()}合并APNG成功！"
            f"[{detail}] → [{apng_filepath}]",
            "finish",
        )

        # 按需删除原图
        img_path_ls += img_dir_ls
        self.execute_deletion(img_path_ls)

    def write_img_2_apng(self, apng_filepath, album, photo, duration, loop, scale,
                         cover_path=None, cover_duration=None):
        from PIL import Image

        # 收集所有需要合并的图片所在目录（与 Img2pdfPlugin 相同的目录收集逻辑）
        if album is None:
            img_dir_ls = [self.option.decide_image_save_dir(photo)]
        else:
            img_dir_ls = [self.option.decide_image_save_dir(photo) for photo in album]

        img_path_ls = []
        for img_dir in img_dir_ls:
            imgs = files_of_dir(img_dir)
            if not imgs:
                continue
            img_path_ls += [p for p in imgs if not os.path.basename(p).startswith(".")]

        if len(img_path_ls) == 0 and not cover_path:
            self.log(f"所有文件夹都不存在图片，无法生成apng：{img_dir_ls}", "error")
            return

        # 归一化 duration：支持 int（全局）或 list（逐帧）
        duration_list = _normalize_duration(duration)

        # 加载封面帧（首帧），再加载正片
        frames = []
        if cover_path and os.path.isfile(cover_path):
            cover = self._load_cover(cover_path, scale)
            if cover is not None:
                frames.append(cover)
                if cover_duration is not None:
                    # 封面帧的时长与正片帧可以不同
                    duration_list = [int(cover_duration)] + duration_list

        frames += self._load_frames(img_path_ls, scale)
        if not frames:
            self.log("没有任何图片可以被打开，无法生成apng", "error")
            return

        # duration 列表需与帧数对齐（不足则用最后一个值补齐）
        if isinstance(duration_list, list):
            if len(duration_list) < len(frames):
                duration_list = duration_list + [duration_list[-1]] * (len(frames) - len(duration_list))
            duration_list = duration_list[:len(frames)]

        # APNG 的所有帧必须是同一尺寸：各章节页面尺寸可能不一致，统一垫到最大尺寸
        max_w = max(f.width for f in frames)
        max_h = max(f.height for f in frames)
        need_pad = any(f.size != (max_w, max_h) for f in frames)
        if need_pad:
            padded = []
            for f in frames:
                if f.size == (max_w, max_h):
                    padded.append(f)
                else:
                    canvas = Image.new("RGB", (max_w, max_h), "white")
                    canvas.paste(f, ((max_w - f.width) // 2, (max_h - f.height) // 2))
                    f.close()
                    padded.append(canvas)
            frames = padded

        try:
            first, rest = frames[0], frames[1:]
            first.save(
                apng_filepath,
                format="PNG",
                save_all=True,
                append_images=rest,
                duration=duration_list,
                loop=int(loop),
            )
        finally:
            for f in frames:
                f.close()

        return img_path_ls, img_dir_ls

    def _load_cover(self, cover_path, scale):
        """加载封面帧，作为 APNG 的第一帧。"""
        from PIL import Image
        try:
            im = Image.open(cover_path)
            im.load()
            if getattr(im, "is_animated", False):
                im.seek(0)
                im.load()
            rgb = im.convert("RGB")
            im.close()
            return _maybe_scale(rgb, scale)
        except Exception as e:
            self.log(f"加载封面失败 {cover_path}: {e}", "error")
            return None

    @staticmethod
    def _load_frames(img_paths, scale):
        from PIL import Image

        frames = []
        for p in img_paths:
            try:
                im = Image.open(p)
                im.load()
                # 如果页面本身是动图（gif），只取第一帧
                if getattr(im, "is_animated", False):
                    im.seek(0)
                    im.load()
                rgb = im.convert("RGB")
                im.close()
                frames.append(_maybe_scale(rgb, scale))
            except Exception as e:
                jm_log("img2apng", f"打开图片失败 {p}: {e}")

        return frames


# 把自定义插件注册进 jmcomic 插件体系，并暴露成 Feature 使用
JmModuleConfig.register_plugin(Img2ApngPlugin)
Feature.export_apng = PluginFeature(Img2ApngPlugin.plugin_key)


# ---------------------------------------------------------------------------
# 目录规划
# ---------------------------------------------------------------------------

# jmcomic 会在打包后自动删除原图（由插件执行）
EXPORT_PLUGIN_KEYS = {"img2pdf", "img2apng", "zip", "long_img"}


def build_probe_option(option_file):
    """构造用于探测漫画元信息的基础 option。"""
    if option_file:
        return jmcomic.create_option_by_file(option_file)
    # 默认使用纯 Python 的 requests 传输层：Android 环境没有 curl_cffi（Rust 原生库），
    # 而 requests 传输层已实测可正常下载；如需恢复 curl_cffi，用 --option 指定即可。
    return jmcomic.JmOption.construct(
        {"client": {"impl": "api", "postman": {"type": "requests", "proxies": {}}}},
    )


def get_album_meta(option, jmid):
    """获取漫画元信息（标题/作者等）。"""
    client = option.build_jm_client()
    return client.get_album_detail(jmid)


def compute_album_root(album, output_dir, flat=False):
    """
    计算“本子的归类根目录”。
    默认按 输出目录/作者/[JM车号]标题 分类；--flat 时直接放 输出目录/[JM车号]标题。

    注意：decide_album_root_dir 只会保留 Bd 和 A* 规则，
    因此“[JM{Aid}]{Atitle}”这种 f-string 规则需要单独用
    DirRule.apply_rule_to_filename 计算（只作用于文件名，不进目录层级）。
    """
    if flat:
        author_root = output_dir
    else:
        opt = jmcomic.JmOption.construct(
            {"dir_rule": {"rule": "Bd/Aauthor", "base_dir": output_dir}},
        )
        author_root = opt.dir_rule.decide_album_root_dir(album)

    name = DirRule.apply_rule_to_filename(album, None, "[JM{Aid}]{Atitle}")
    return os.path.join(author_root, name)


def build_download_option(base_option, album_root):
    """
    构造真正用于下载的 option：
    - 图片保存路径固定为 album_root/<章节序号>/
    - 保留用户自定义配置（client/download/plugins），但去掉导出类插件，
      避免与我们的 --format 重复导出。
    """
    plugins = dict(base_option.plugins.src_dict or {})
    cleaned = {}
    for when, plist in plugins.items():
        if not isinstance(plist, list):
            cleaned[when] = plist
            continue
        kept = [p for p in plist
                if not (isinstance(p, dict) and p.get("plugin") in EXPORT_PLUGIN_KEYS)]
        if kept:
            cleaned[when] = kept

    return jmcomic.JmOption.construct(
        {
            "dir_rule": {"rule": "Bd/Pindex", "base_dir": album_root},
            "client": base_option.client.src_dict,
            "download": base_option.download.src_dict,
            "plugins": cleaned,
        }
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_features(fmt, album_root, duration, loop, scale, delete_original,
                   cover_path=None, cover_duration=None):
    """根据目标格式构建 Feature 列表。

    注意顺序：APNG 在 PDF 之前。当 fmt=both 且 delete_original 时，
    只让最后一个 Feature（PDF）负责删除原图——因为两个插件都要读取同一批源图，
    先执行者一旦删图，后执行者就会无图可用。
    """
    apng_kw = dict(img_dir=album_root, duration=duration, loop=loop, scale=scale)
    if cover_path:
        apng_kw["cover_path"] = cover_path
    if cover_duration is not None:
        apng_kw["cover_duration"] = cover_duration
    pdf_kw = dict(pdf_dir=album_root)

    if fmt == "apng":
        if delete_original:
            apng_kw["delete_original_file"] = True
        return [Feature.export_apng(**apng_kw)]

    if fmt == "pdf":
        if delete_original:
            pdf_kw["delete_original_file"] = True
        return [Feature.export_pdf(**pdf_kw)]

    # both
    features = [Feature.export_apng(**apng_kw)]
    if delete_original:
        pdf_kw["delete_original_file"] = True
    features.append(Feature.export_pdf(**pdf_kw))
    return features


def verify_outputs(album_root, fmt):
    """下载与打包完成后，校验要求的产物确实存在，避免“静默失败”。"""
    produced = {}
    if fmt in ("pdf", "both"):
        pdfs = [f for f in os.listdir(album_root) if f.lower().endswith(".pdf")]
        if not pdfs:
            raise RuntimeError("PDF 未生成成功")
        produced["pdf"] = pdfs[0]
    if fmt in ("apng", "both"):
        apngs = [f for f in os.listdir(album_root) if f.lower().endswith(".apng")]
        if not apngs:
            raise RuntimeError("APNG 未生成成功")
        produced["apng"] = apngs[0]
    return produced


def fetch_cover_auto(client, album, album_root):
    """下载本子封面为临时文件，作为 APNG 首帧；失败返回 None。"""
    tmp = os.path.join(album_root, f".cover_{album.id}.jpg")
    try:
        client.download_album_cover(album.id, tmp)
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            return tmp
    except Exception as e:
        jm_log("jm_export", f"封面下载失败，跳过封面: {e}")
    return None


def run_download(jmid, fmt, output_dir, duration, loop, scale, delete_original,
                 photo_id, option_file, flat, cover, cover_duration):
    probe_option = build_probe_option(option_file)
    client = probe_option.build_jm_client()

    # 1. 拿到元信息，规划归类目录
    if photo_id:
        photo = client.get_photo_detail(photo_id)
        album = photo.from_album
    else:
        album = get_album_meta(probe_option, jmid)

    album_root = compute_album_root(album, output_dir, flat=flat)
    jm_log("jm_export", f"漫画: [{album.id}] {album.title}")
    jm_log("jm_export", f"归类目录: {album_root}")

    # 2. 处理封面首帧（auto=下载本子封面；否则视为本地图片路径）
    cover_path = None
    if cover:
        if cover == "auto":
            cover_path = fetch_cover_auto(client, album, album_root)
        elif os.path.isfile(cover):
            cover_path = os.path.abspath(cover)
        else:
            jm_log("jm_export", f"封面文件不存在，忽略: {cover}")
    if cover_path:
        jm_log("jm_export", f"APNG 首帧封面: {cover_path}")

    # 3. 构造下载 option 并执行
    download_option = build_download_option(probe_option, album_root)
    features = build_features(fmt, album_root, duration, loop, scale, delete_original,
                              cover_path=cover_path, cover_duration=cover_duration)
    extra = features[0] if len(features) == 1 else features

    try:
        if photo_id:
            result = download_photo(photo_id, download_option, extra=extra, check_exception=True)
        else:
            result = download_album(jmid, download_option, extra=extra, check_exception=True)
    finally:
        # 清理临时封面文件
        if cover_path and cover_path.startswith(album_root):
            base = os.path.basename(cover_path)
            if base.startswith(".cover_"):
                try:
                    os.remove(cover_path)
                except OSError:
                    pass

    detail = result.detail

    # 校验要求的产物确实生成
    produced = verify_outputs(album_root, fmt)
    jm_log("jm_export", f"已生成产物: {', '.join(f'{k}={v}' for k, v in produced.items())}")

    jm_log("jm_export", "下载完成，输出如下：")
    _print_tree(album_root)
    return album_root, detail, produced


def show_info(jmid, option_file):
    """仅查看本子元信息，不下载。"""
    probe_option = build_probe_option(option_file)
    album = get_album_meta(probe_option, jmid)
    photos = list(album)
    lines = [
        "=" * 46,
        f"标题   : {album.title}",
        f"车号   : {album.id}",
        f"作者   : {album.author}",
        f"章节数 : {len(photos)}",
    ]
    if album.page_count:
        lines.append(f"总页数 : {album.page_count}")
    if album.pub_date and album.pub_date != "0":
        lines.append(f"发布   : {album.pub_date}")
    if album.update_date and album.update_date != "0":
        lines.append(f"更新   : {album.update_date}")
    if album.tags:
        lines.append(f"标签   : {', '.join(album.tags)}")
    lines.append("=" * 46)
    for i, p in enumerate(photos, 1):
        lines.append(f"  [{i}] 章节 {p.id}: {p.title}")
    return "\n".join(lines)


def _print_tree(root):
    """简要打印输出目录结构。"""
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        depth = dirpath[len(root):].count(os.sep)
        indent = "  " * depth
        lines.append(f"{indent}{os.path.basename(dirpath) or root}/")
        for fn in sorted(filenames):
            lines.append(f"{indent}  {fn}")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="jm_export",
        description="使用 jmcomic 下载漫画，并导出为 PDF / APNG 动画。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("jmid", nargs="+", help="禁漫车号（本子的唯一标识符），可传多个实现批量下载，如 350234 350235")
    parser.add_argument(
        "-f", "--format", choices=["pdf", "apng", "both"], default="both",
        help="导出格式：pdf / apng / both（默认 both）",
    )
    parser.add_argument(
        "-o", "--output", default="./jm_output",
        help="输出根目录（默认 ./jm_output）",
    )
    parser.add_argument(
        "--duration", type=_normalize_duration, default=400,
        help="APNG 帧停留时长（毫秒）。单个数字=所有帧相同；"
             "逗号分隔的列表=逐帧时长，如 800,400,400（默认 400）",
    )
    parser.add_argument(
        "--cover", default=None, metavar="auto|图片路径",
        help="给 APNG 添加首帧封面：auto=自动下载本子封面；或传入本地图片路径",
    )
    parser.add_argument(
        "--cover-duration", type=int, default=None, metavar="毫秒",
        help="APNG 封面首帧的停留时长，默认与 --duration 相同",
    )
    parser.add_argument(
        "--loop", type=int, default=0,
        help="APNG 循环次数，0 表示无限循环（默认 0）",
    )
    parser.add_argument(
        "--scale", type=float, default=1.0,
        help="APNG 帧的缩放比例（0<scale<=1 可显著减小体积，默认 1.0）",
    )
    parser.add_argument(
        "--delete-original", action="store_true",
        help="打包成功后删除原始图片",
    )
    parser.add_argument(
        "--photo", dest="photo_id", default=None,
        help="只下载并打包指定章节（章节车号），默认处理整本",
    )
    parser.add_argument(
        "--option", dest="option_file", default=None,
        help="jmcomic 自定义配置文件路径（yaml），用于代理/域名/登录等",
    )
    parser.add_argument(
        "--flat", action="store_true",
        help="不按作者分组，直接输出到 输出目录/[JM车号]标题",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="仅查看本子元信息（标题/作者/章节等），不下载",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.scale <= 0:
        print("错误：--scale 必须大于 0", file=sys.stderr)
        return 2

    # 仅查看元信息
    if args.info:
        try:
            for jmid in args.jmid:
                print(show_info(jmid, args.option_file))
        except jmcomic.JmcomicException as e:
            print(f"查询失败：{e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"发生未预期错误：{type(e).__name__}: {e}", file=sys.stderr)
            return 1
        return 0

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    failed = []
    for jmid in args.jmid:
        print(f"\n>>> 开始处理车号 [{jmid}] <<<")
        try:
            album_root, detail, produced = run_download(
                jmid=jmid,
                fmt=args.format,
                output_dir=output_dir,
                duration=args.duration,
                loop=args.loop,
                scale=args.scale,
                delete_original=args.delete_original,
                photo_id=args.photo_id,
                option_file=args.option_file,
                flat=args.flat,
                cover=args.cover,
                cover_duration=args.cover_duration,
            )
            results.append((jmid, album_root, detail, produced))
        except jmcomic.JmcomicException as e:
            failed.append((jmid, f"下载失败: {e}"))
        except Exception as e:
            failed.append((jmid, f"{type(e).__name__}: {e}"))

    # 汇总
    print("\n" + "=" * 50)
    print("处理汇总")
    print("=" * 50)
    for jmid, album_root, detail, produced in results:
        prods = ", ".join(f"{k}={v}" for k, v in produced.items())
        print(f"  [OK]  {jmid} → {album_root}")
        print(f"        产物: {prods}")
    for jmid, err in failed:
        print(f"  [FAIL] {jmid} → {err}")
    print("=" * 50)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
