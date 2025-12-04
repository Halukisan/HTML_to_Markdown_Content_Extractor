from queue import Queue
from threading import Lock
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

class WebDriverPool:
    def __init__(self, pool_size=3):
        self.pool_size = pool_size
        self.drivers = Queue()
        self.lock = Lock()
        self._init_drivers()
    
    def _create_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        # 为每个driver分配不同的端口
        chrome_options.add_argument(f"--remote-debugging-port={9222 + self.drivers.qsize()}")
        return webdriver.Chrome(options=chrome_options)
    
    def _init_drivers(self):
        for _ in range(self.pool_size):
            driver = self._create_driver()
            self.drivers.put(driver)
    
    def get_driver(self):
        return self.drivers.get()
    
    def return_driver(self, driver):
        self.drivers.put(driver)
    
    def close_all(self):
        while not self.drivers.empty():
            driver = self.drivers.get()
            try:
                driver.quit()
            except:
                pass
