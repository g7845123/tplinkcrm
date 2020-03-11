import requests
from bs4 import BeautifulSoup
import re
import random

def query_online_price(country, account, link, header, proxies=None):
    price = None
    # DE
    if country == 'DE': 
        # AMZ 
        if account == 'AMZ':
            response = requests.get(link, headers=header, proxies=proxies, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html.parser')
            # Deal price
            deal_block = parsed_html.find('span', {"id": "priceblock_dealprice"})
            if deal_block:
                ele = deal_block
            else:
                ele = parsed_html.find('span', {"id": "priceblock_ourprice"})
            if ele:
                text = ele.get_text()
                text = text.replace('.', '').replace(',', '.')
                price = re.findall("\d+\.\d+", text)[0]
                price = round(float(price)*100)
            print(account, link, response.status_code, price)
    return price

if __name__ == "__main__":
    header = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.132 Safari/537.36'
    }
    query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B07FY5X9P6/', header)
