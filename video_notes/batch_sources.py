"""展开 B站合集/多 P 与本地目录，返回可复用的单视频输入。"""
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from .bili_auth import load_jar

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.ts', '.m4v', '.wmv', '.mpeg', '.mpg'}


def natural_key(value):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r'(\d+)', str(value))]


def canonical_video_input(value):
    value = str(value).strip()
    if re.fullmatch(r'BV[0-9A-Za-z]{10}|av\d+', value):
        return 'https://www.bilibili.com/video/' + value
    parsed = urlparse(value)
    match = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10}|av\d+)/?', parsed.path)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('bilibili.com', 'www.bilibili.com') or not match:
        raise ValueError('B站输入应为 BV 号或视频链接；合集请先读取视频列表')
    result = 'https://www.bilibili.com/video/' + match[1]
    part = (parse_qs(parsed.query).get('p') or [''])[0]
    if part:
        if not part.isdigit() or int(part) < 1:
            raise ValueError('视频分 P 编号必须为正整数')
        result += '?p=' + str(int(part))
    return result


def directory_videos(value, recursive=False):
    if not str(value).strip():
        raise ValueError('请先选择或填写本地视频目录')
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('目录不存在，请选择本地视频所在目录')
    files = []
    def failed(error):
        raise ValueError('无法读取目录：' + str(error.filename)) from error
    for current, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()] if recursive else []
        for name in names:
            path = Path(current) / name
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file() and not path.is_symlink():
                files.append(path)
    files.sort(key=lambda p: natural_key(p.relative_to(root).as_posix()))
    if not files:
        raise ValueError('该目录没有可处理的视频' + ('（已包含子目录）' if recursive else '，可尝试勾选“包含子目录”'))
    return {'title': root.name or str(root), 'inputs': [
        {'input': str(path), 'isLocal': True, 'title': path.stem} for path in files]}


def collection_videos(value, cookies):
    import requests
    session = requests.Session()
    try:
        session.cookies.update(load_jar(cookies))
    except (OSError, ValueError):
        pass
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    try:
        return _collection_videos(value, lambda endpoint, params: _get(session, endpoint, params))
    finally:
        session.close()


def _get(session, endpoint, params):
    try:
        response = session.get('https://api.bilibili.com' + endpoint, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        raise ValueError('读取 B站合集失败，请检查网络、登录状态后重试') from error
    if payload.get('code') != 0:
        raise ValueError('B站未能返回合集：' + str(payload.get('message') or '请重新登录或稍后重试'))
    return payload.get('data') or {}


def video_input(entry):
    bvid = str(entry.get('bvid') or '')
    if not re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid):
        aid = entry.get('aid')
        if not str(aid or '').isdigit():
            raise ValueError('合集中存在无法识别的视频，请检查是否已删除或无访问权限')
        bvid = 'av' + str(aid)
    return {'input': 'https://www.bilibili.com/video/' + bvid,
            'isLocal': False, 'title': entry.get('title') or bvid}


def _collection_videos(value, get):
    value = str(value).strip()
    if re.fullmatch(r'BV[0-9A-Za-z]{10}|av\d+', value):
        value = 'https://www.bilibili.com/video/' + value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('www.bilibili.com', 'bilibili.com', 'space.bilibili.com'):
        raise ValueError('请填写 B站合集链接，或合集内视频的 BV 号/链接')
    query = parse_qs(parsed.query)
    match = re.fullmatch(r'/(\d+)/(?:lists/(\d+)|channel/(collectiondetail|seriesdetail))/?', parsed.path)
    if match:
        mid = match[1]
        sid = match[2] or (query.get('sid') or [''])[0]
        if not sid.isdigit():
            raise ValueError('合集链接缺少有效的合集编号')
        series = match[3] == 'seriesdetail' or query.get('type') == ['series']
        return _list_videos(mid, sid, series, get)
    video = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10}|av\d+)/?', parsed.path)
    if not video:
        raise ValueError('请使用合集详情页链接，或合集内的视频链接')
    identifier = video[1]
    data = get('/x/web-interface/view', {'bvid': identifier} if identifier.startswith('BV') else {'aid': identifier[2:]})
    season = data.get('ugc_season') or {}
    if season.get('id') and (season.get('mid') or (data.get('owner') or {}).get('mid')):
        return _list_videos(str(season.get('mid') or data['owner']['mid']), str(season['id']), False, get)
    pages = data.get('pages') or []
    if len(pages) > 1:
        bvid = data.get('bvid') or identifier
        return {'title': data.get('title') or '多 P 视频', 'inputs': [
            {'input': 'https://www.bilibili.com/video/' + bvid + '?p=' + str(page['page']),
             'isLocal': False, 'title': page.get('part') or '第 %s P' % page['page']} for page in pages]}
    raise ValueError('该视频未找到合集或多 P 列表，请使用“B站视频”或粘贴合集详情页链接')


def _list_videos(mid, sid, series, get):
    inputs, seen, title, page = [], set(), '', 1
    if series:
        title = (get('/x/series/series', {'series_id': sid}).get('meta') or {}).get('name') or ''
    while True:
        if series:
            data = get('/x/series/archives', {'mid': mid, 'series_id': sid, 'pn': page, 'ps': 100, 'sort': 'asc', 'only_normal': 'true'})
        else:
            data = get('/x/polymer/web-space/seasons_archives_list', {'mid': mid, 'season_id': sid, 'page_num': page, 'page_size': 100, 'sort_reverse': 'false'})
            title = title or (data.get('meta') or {}).get('name') or ''
        entries = data.get('archives') or []
        total = (data.get('page') or {}).get('total')
        if total is None:
            raise ValueError('B站返回的合集列表不完整，请稍后重新读取')
        added = 0
        for entry in entries:
            item = video_input(entry)
            if item['input'] not in seen:
                seen.add(item['input'])
                inputs.append(item)
                added += 1
        if len(inputs) >= int(total):
            break
        if not added:
            raise ValueError('合集列表未读取完整，请重新登录或稍后重试')
        page += 1
    if not inputs:
        raise ValueError('合集内没有可读取的视频')
    return {'title': title or 'B站合集', 'inputs': inputs}
