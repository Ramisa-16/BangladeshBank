import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


URL = "https://www.bb.org.bd/en/index.php/mediaroom/circular"

SAVE_FOLDER = r"C:\Users\Ramisa\Desktop\bb_pdfs"
os.makedirs(SAVE_FOLDER, exist_ok=True)

driver = webdriver.Chrome()
wait = WebDriverWait(driver, 20)

driver.get(URL)

wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))

all_links = set()


def extract():
    rows = driver.find_elements(By.XPATH, "//table//tr")
    for row in rows:
        for a in row.find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if href and ".pdf" in href:
                all_links.add(href)


last_count = 0
stuck_counter = 0

while True:
    extract()

    current = len(all_links)
    print(f"[Collected] {current}")

    # 🔥 stop if no progress for multiple iterations
    if current == last_count:
        stuck_counter += 1
    else:
        stuck_counter = 0

    last_count = current

    if stuck_counter >= 3:
        print("No new data detected → stopping")
        break

    try:
        next_btn = driver.find_element(By.XPATH, "//a[contains(text(),'Next')]")

        driver.execute_script("arguments[0].click();", next_btn)
        time.sleep(3)

    except:
        break


driver.quit()

print("\nTOTAL PDFs:", len(all_links))


# DOWNLOAD
for url in all_links:
    name = url.split("/")[-1]
    path = os.path.join(SAVE_FOLDER, name)

    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            print("[OK]", name)
    except:
        print("[FAIL]", name)