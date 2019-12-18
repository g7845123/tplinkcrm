import requests
from bs4 import BeautifulSoup
import re
import random

def query_online_price(country, account, link, header, proxies):
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
    print(query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B00A0VCJPI/'))
    print(query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B00KXULGJQ/'))
    print(query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B00K11UHVA/'))
    print(query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B07FY5X9P6/'))
