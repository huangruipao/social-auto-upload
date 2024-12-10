import time
from datetime import datetime, timedelta
import requests
import asyncio
import ast
import os
from pathlib import Path
import shutil
import zipfile
import uuid
import re
import json
import subprocess
from apscheduler.schedulers.blocking import BlockingScheduler
from conf import BASE_DIR
from uploader.douyin_uploader.main import DouYinVideo, douyin_setup
from uploader.bilibili_uploader.main import read_cookie_json_file, extract_keys_from_json, random_emoji, BilibiliUploader
from uploader.ks_uploader.main import KSVideo, ks_setup
from uploader.tencent_uploader.main import TencentVideo, weixin_setup
from uploader.tk_uploader.main_chrome import TiktokVideo, tiktok_setup
from utils.files_times import generate_schedule_time_next_day, get_title_and_hashtags
from utils.constant import VideoZoneTypes, TencentZoneTypes
import configparser
from xhs import XhsClient
from uploader.xhs_uploader.main import sign, beauty_print

config = configparser.RawConfigParser()
config.read(Path(BASE_DIR / "uploader" / "xhs_uploader" / "accounts.ini"))

base_url = 'http://192.168.10.254:2000'


def write_to_file(file_path, file_name, text, mode='a'):
    complete_path = Path(file_path) / f'{file_name}.txt'
    try:
        with open(complete_path, mode, encoding='utf-8') as f:
            f.write(text + '\n')
    except IOError as e:
        print(f"写入文件失败：{e}")


def get_cookie_data(date_cookies, account_file, cookie_setup):
        date_cookies = json.loads(date_cookies) if date_cookies else None
        # 判断account_file文件是否存在如果不存在则创建文件及文件夹
        if not os.path.exists(account_file):
            os.makedirs(os.path.dirname(account_file), exist_ok=True)
            with open(account_file, 'w', encoding='utf-8') as f:
                json.dump(date_cookies, f)
        if date_cookies is None and callable(cookie_setup) :
            asyncio.run(cookie_setup(account_file, handle=True))
        elif date_cookies is None and cookie_setup =='bilibili_setup':
            subprocess.run([f'{BASE_DIR}/uploader/bilibili_uploader/biliup.exe', '-u', account_file, 'login'],check=True)

async def upload_single_video(file, video_md5, shipinhao_user_id, shipinhao_num, account_file):
    try:
        cookie_data = read_cookie_json_file(account_file)
        print(f"视频上传成功：{file}")
        upload_data = {
            'video_md5': video_md5,
            'shipinhao_user_id': shipinhao_user_id,
            'cookie_data': cookie_data,
            'shipinhao_num': shipinhao_num,
        }
        response = requests.post(f'{base_url}/published', json=upload_data)
        response.raise_for_status()
        print('响应数据:', response.text)
    except requests.RequestException as e:
        print(f"上传过程中发生错误：{e}")


def upload_to_platform(put_info):
    if not put_info:
        return

    local_path = Path.cwd() / 'output'
    local_path.mkdir(parents=True, exist_ok=True)

    for info in put_info:
        shipinhao_num = info.get('shipinhao_num', {})
        shipinhao_info_list = [info.get('shipinhao_info', {})] + info.get('sub_shipinhao_info', [])
        video_paths = info.get('deduplicated_video_path', '').split(',')
        title = info.get('video_title', '')
        video_md5 = info.get('video_md5', '')
        target_pub_user_id = info.get('target_pub_user_id', '') or ""

        for filepath, shipinhao_list in zip(video_paths, shipinhao_info_list):
            file_path = filepath.split('download_video/')[1].replace('.mp4', '.zip')
            file_url = f'{base_url}/files/{file_path}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
            }
            try:
                response = requests.get(file_url, headers=headers, stream=True)
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"Failed to download file from {file_url}, status code: {response.status_code}")
                continue

            file_zip = local_path / file_path
            file_zip.parent.mkdir(parents=True, exist_ok=True)
            content_type = response.headers.get('Content-Type')
            print(f"Response Content-Type: {content_type}")

            with open(file_zip, 'wb') as f:
                shutil.copyfileobj(response.raw, f)
            print(f"Downloaded: {file_zip}")

            if file_zip.suffix == '.zip':
                with zipfile.ZipFile(file_zip, 'r') as zip_ref:
                    zip_ref.extractall(file_zip.parent)
                print(f"Extracted contents of {file_zip} to {file_zip.parent}")
                file_zip.unlink()
                print(f"Deleted ZIP file: {file_zip}")

            file = file_zip.with_suffix('.mp4')
            file_name = file.stem
            folder_path = file.parent
            write_to_file(folder_path, file_name, title, mode='w')
            tags = info.get('video_tags', '')
            write_to_file(folder_path, file_name, tags)

            title, tags = get_title_and_hashtags(str(file))
            thumbnail_path = file.with_suffix('.png')

            print(f"视频文件名：{file}")
            print(f"标题：{title}")
            print(f"Hashtag:{tags}")
            for shipinhao in shipinhao_list:
                if shipinhao['shipinhao_user_id'] not in target_pub_user_id:
                    try:
                        publish_hours_list = ast.literal_eval(shipinhao.get('publish_hours', '[]'))
                        publish_datetimes = generate_schedule_time_next_day(len(video_paths), 1, daily_times=publish_hours_list)

                        if shipinhao['shipinhao_platform'] == 'douyin':
                            account_file = Path(BASE_DIR / "cookies" / "douyin_uploader" / "account.json")
                            get_cookie_data(shipinhao.get('cookies', ''), account_file, douyin_setup)
                            app_args = [title, file, tags, publish_datetimes[0], account_file]
                            if thumbnail_path.exists():
                                app_args.append(thumbnail_path)
                            app = DouYinVideo(*app_args)
                            asyncio.run(app.main(), debug=False)

                        elif shipinhao['shipinhao_platform'] == 'bilibili':
                            account_file = Path(BASE_DIR / "cookies" / "bilibili_uploader" / "account.json")
                            get_cookie_data(shipinhao.get('cookies', ''), account_file, 'bilibili_setup')
                            if not account_file.exists():
                                print(f"{account_file.name} 配置文件不存在")
                                continue
                            cookie_data = extract_keys_from_json(read_cookie_json_file(account_file))
                            timestamps = generate_schedule_time_next_day(len(video_paths), 1, daily_times=publish_hours_list, timestamps=True)
                            tid = VideoZoneTypes.SPORTS_FOOTBALL.value
                            title += random_emoji()
                            limited_tags = tags[:10] if len(tags) > 10 else tags
                            tags_str = ','.join(limited_tags)
                            desc = title
                            bili_uploader = BilibiliUploader(cookie_data, file, title, desc, tid, limited_tags, timestamps[0])
                            bili_uploader.upload()
                            time.sleep(30)

                        elif shipinhao['shipinhao_platform'] == 'kuaishou':
                            account_file = Path(BASE_DIR / "cookies" / "ks_uploader" / "account.json")
                            get_cookie_data(shipinhao.get('cookies', ''), account_file, ks_setup)
                            app = KSVideo(title, file, tags, publish_datetimes[0], account_file)
                            asyncio.run(app.main(), debug=False)

                        elif shipinhao['shipinhao_platform'] == 'qq':
                            account_file = Path(BASE_DIR / "cookies" / "tencent_uploader" / "account.json")
                            get_cookie_data(shipinhao.get('cookies', ''), account_file, weixin_setup)
                            category = TencentZoneTypes.LIFESTYLE.value
                            app = TencentVideo(title, file, tags, publish_datetimes[0], account_file, category)
                            asyncio.run(app.main(), debug=False)

                        elif shipinhao['shipinhao_platform'] == 'tiktok':
                            account_file = Path(BASE_DIR / "cookies" / "tk_uploader" / "account.json")
                            get_cookie_data(shipinhao.get('cookies', ''), account_file, tiktok_setup)
                            app = TiktokVideo(title, file, tags, publish_datetimes[0], account_file, thumbnail_path if thumbnail_path.exists() else None)
                            asyncio.run(app.main(), debug=False)

                        elif shipinhao['shipinhao_platform'] == 'xiaohongshu':
                            cookies = config['account1']['cookies']
                            xhs_client = XhsClient(cookies, sign=sign, timeout=60)
                            xhs_client.get_video_first_frame_image_id("3214")  # 检查cookie有效性
                            tags_str = ' '.join(['#' + tag for tag in tags])
                            hash_tags = [xhs_client.get_suggest_topic(tag)[0]['name'] for tag in tags[:3] if xhs_client.get_suggest_topic(tag)]
                            hash_tags_str = ' '.join([f"#{tag}[话题]#" for tag in hash_tags])
                            note = xhs_client.create_video_note(
                                title=title[:20],
                                video_path=str(file),
                                desc=f"{title}{tags_str} {hash_tags_str}",
                                topics=hash_tags,
                                is_private=False,
                                post_time=publish_datetimes[0].strftime("%Y-%m-%d %H:%M:%S")
                            )
                            beauty_print(note)
                            time.sleep(30)  # 避免风控

                        asyncio.run(upload_single_video(file, video_md5, shipinhao['shipinhao_user_id'], shipinhao_num, account_file))
                    except Exception as e:
                        platform_name = {
                            'douyin': '抖音',
                            'bilibili': '哔哩哔哩',
                            'kuaishou': '快手',
                            'qq': '视频号',
                            'tiktok': 'tiktok',
                            'xiaohongshu': '小红书'
                        }.get(shipinhao['shipinhao_platform'], '未知平台')
                        print(f"{platform_name}上传失败：{e}")
                else:
                    print(f"该用户已上传过该视频：{shipinhao['shipinhao_user_id']}")


def upload_platform():
    mac_address = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
    print(f"MAC地址: {mac_address}")
    data = {'machine_seq': mac_address}
    url = f'{base_url}/pub'

    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        print('响应数据:', response.json())
        upload_to_platform(response.json())
    except requests.RequestException as e:
        print(f"网络请求失败：{e}")


if __name__ == '__main__':
    scheduler = BlockingScheduler()
    now = datetime.now()
    initial_execution_time = now.replace(minute=now.minute + 1, second=0, microsecond=0)
    scheduler.add_job(upload_platform, 'interval', minutes=4, start_date=initial_execution_time)
    scheduler.start()
