import os
import time
import random
import logging
import platform
import requests
import html
import io
from datetime import datetime
from configparser import ConfigParser
from tabulate import tabulate
from playwright.sync_api import sync_playwright, TimeoutError

# 创建一个 StringIO 对象用于捕获日志
log_stream = io.StringIO()

# 创建日志记录器
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 创建控制台输出的处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 创建 log_stream 处理器
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setLevel(logging.INFO)

# 创建格式化器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 为处理器设置格式化器
console_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# 将处理器添加到日志记录器中
logger.addHandler(console_handler)
logger.addHandler(stream_handler)

# 自动判断运行环境
IS_GITHUB_ACTIONS = 'GITHUB_ACTIONS' in os.environ
IS_SERVER = platform.system() == "Linux" and not IS_GITHUB_ACTIONS

# 从配置文件或环境变量中读取配置信息
def load_config():
    config = ConfigParser()
    if IS_SERVER:
        config_file = './config.ini'
    elif IS_GITHUB_ACTIONS:
        config_file = None
    else:
        config_file = 'config.ini'
    
    if config_file and os.path.exists(config_file):
        config.read(config_file)
    
    return config

config = load_config()

USERNAME = os.getenv("LINUXDO_USERNAME", config.get('credentials', 'username', fallback=None))
PASSWORD = os.getenv("LINUXDO_PASSWORD", config.get('credentials', 'password', fallback=None))
LIKE_PROBABILITY = float(os.getenv("LIKE_PROBABILITY", config.get('settings', 'like_probability', fallback='0.02')))
HOME_URL = config.get('urls', 'home_url', fallback="https://linux.do/")
CONNECT_URL = config.get('urls', 'connect_url', fallback="https://connect.linux.do/")
USE_WXPUSHER = os.getenv("USE_WXPUSHER", config.get('wxpusher', 'use_wxpusher', fallback='false')).lower() == 'true'
APP_TOKEN = os.getenv("APP_TOKEN", config.get('wxpusher', 'app_token', fallback=None))
TOPIC_ID = os.getenv("TOPIC_ID", config.get('wxpusher', 'topic_id', fallback=None))
MAX_TOPICS = int(os.getenv("MAX_TOPICS", config.get('settings', 'max_topics', fallback='10')))

# 检查必要配置
missing_configs = []

if not USERNAME:
    missing_configs.append("USERNAME")
if not PASSWORD:
    missing_configs.append("PASSWORD")
if USE_WXPUSHER and not APP_TOKEN:
    missing_configs.append("APP_TOKEN")
if USE_WXPUSHER and not TOPIC_ID:
    missing_configs.append("TOPIC_ID")

if missing_configs:
    logging.error(f"缺少必要配置: {', '.join(missing_configs)}，请在环境变量或配置文件中设置。")
    exit(1)

class NotificationManager:
    def __init__(self, use_wxpusher, app_token, topic_id):
        self.use_wxpusher = use_wxpusher
        self.app_token = app_token
        self.topic_id = topic_id
    
    def send_message(self, content, summary):
        if self.use_wxpusher:
            try:
                data = {
                    "appToken": self.app_token,
                    "content": content,
                    "summary": summary,
                    "contentType": 2,
                    "topicIds": [self.topic_id],
                    "verifyPayType": 0
                }
                # 使用单独的请求日志记录器来避免混淆
                request_logger = logging.getLogger("request_logger")
                request_logger.info("发送 wxpusher 消息...")
                response = requests.post("https://wxpusher.zjiecode.com/api/send/message", json=data)

                if response.status_code == 200:
                    request_logger.info("wxpusher 消息发送成功")
                else:
                    request_logger.error(f"wxpusher 消息发送失败: {response.status_code}, {response.text}")
                    
            except Exception as e:
                request_logger.error(f"发送 wxpusher 消息时出错: {e}")

class LinuxDoBrowser:
    def __init__(self) -> None:
        logging.info("启动 Playwright...")
        self.pw = sync_playwright().start()
        logging.info("以无头模式启动 Firefox...")
        self.browser = self.pw.firefox.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        logging.info(f"导航到 {HOME_URL}...")
        self.page.goto(HOME_URL)
        logging.info("初始化完成。")

    def login(self) -> bool:
        try:
            logging.info("尝试登录...")
            self.page.click(".login-button .d-button-label")
            time.sleep(2)
            self.page.fill("#login-account-name", USERNAME)
            time.sleep(2)
            self.page.fill("#login-account-password", PASSWORD)
            time.sleep(2)
            self.page.click("#login-button")
            time.sleep(10)  # 等待页面加载完成
            user_ele = self.page.query_selector("#current-user")
            if not user_ele:
                logging.error("登录失败")
                return False
            else:
                logging.info("登录成功")
                return True
        except TimeoutError:
            logging.error("登录失败：页面加载超时或元素未找到")
            return False

    def click_topic(self):
        try:
            logging.info("开始处理主题...")
            topics = self.page.query_selector_all("#list-area .title")
            total_topics = len(topics)
            logging.info(f"共找到 {total_topics} 个主题。")

            # 限制处理的最大主题数
            if total_topics > MAX_TOPICS:
                logging.info(f"处理主题数超过最大限制 {MAX_TOPICS}，仅处理前 {MAX_TOPICS} 个主题。")
                topics = topics[:MAX_TOPICS]

            browsed_articles = []
            liked_articles = []
            like_count = 0

            for idx, topic in enumerate(topics):
                article_title = topic.text_content().strip()
                logging.info(f"打开第 {idx + 1}/{len(topics)} 个主题 ：{article_title} ... ")
                page = self.context.new_page()
                article_url = HOME_URL + topic.get_attribute("href")
                browsed_articles.append({"title": article_title, "url": article_url})

                try:
                    page.goto(article_url)
                    time.sleep(3)  # 等待页面完全加载
                    if random.random() < LIKE_PROBABILITY:
                        self.click_like(page)
                        liked_articles.append({"title": article_title, "url": article_url})
                        like_count += 1

                except TimeoutError:
                    logging.warning(f"打开主题 ： {article_title} 超时，跳过该主题。")
                finally:
                    time.sleep(3)  # 等待一段时间，防止操作过快导致出错
                    page.close()
                    logging.info(f"已关闭第 {idx + 1}/{len(topics)} 个主题 ： {article_title} ...")

            # 打印浏览的文章信息
            logging.info("--------------浏览的文章信息-----------------")
            print(tabulate(browsed_articles, headers="keys", tablefmt="pretty"))

            # 打印点赞的文章信息
            logging.info(f"一共点赞了 {like_count} 篇文章。")
            if like_count > 0:
                logging.info("--------------点赞的文章信息-----------------")
                print(tabulate(liked_articles, headers="keys", tablefmt="pretty"))

        except Exception as e:
            logging.error(f"处理主题时出错: {e}")

    def run(self):
        start_time = datetime.now()
        logging.info(f"开始执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            logging.info("开始运行自动化流程...")
            if not self.login():
                return
            self.click_topic()
            self.print_connect_info()
        except Exception as e:
            logging.error(f"运行过程中出错: {e}")
        finally:
            end_time = datetime.now()
            logging.info(f"结束执行时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.context.close()
            self.browser.close()
            self.pw.stop()

            if USE_WXPUSHER:
                elapsed_time = end_time - start_time
                summary = f"Linux.do保活脚本 {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
                
                # 获取并转义日志内容
                log_content = log_stream.getvalue()
                escaped_log_content = html.escape(log_content)
                html_log_content = f"<pre>{escaped_log_content}</pre>"

                # 创建 HTML 格式的内容
                content = (
                    f"<h1>Linux.do保活脚本 {end_time.strftime('%Y-%m-%d %H:%M:%S')}</h1>"
                    f"<br/><p style='color:red;'>"
                    f"开始执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}<br/>"
                    f"结束执行时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}<br/>"
                    f"总耗时: {elapsed_time}<br/>"
                    f"</p>"
                    f"<h2>日志内容</h2>"
                    f"{html_log_content}"
                )
                
                wx_pusher = NotificationManager(USE_WXPUSHER, APP_TOKEN, TOPIC_ID)
                wx_pusher.send_message(content, summary)

    def print_connect_info(self):
        try:
            logging.info(f"导航到 {CONNECT_URL}...")
            self.page.goto(CONNECT_URL)
            time.sleep(2)
            logging.info(f"当前页面URL: {self.page.url}")
        except TimeoutError:
            logging.error("连接信息页面加载超时")
        except Exception as e:
            logging.error(f"打印连接信息时出错: {e}")

    def click_like(self, page):
        try:
            page.wait_for_selector(".discourse-reactions-reaction-button button", timeout=2000)
            like_button = page.locator(".discourse-reactions-reaction-button").first
            if like_button:
                like_button.click()
                logging.info("文章已点赞")
            else:
                logging.info("未找到点赞按钮")
        except TimeoutError:
            logging.warning("点赞按钮定位超时")
        except Exception as e:
            logging.error(f"点赞操作失败: {e}")



if __name__ == "__main__":
    ldb = LinuxDoBrowser()
    ldb.run()