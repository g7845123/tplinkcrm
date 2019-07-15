import requests
from bs4 import BeautifulSoup
import re

def query_online_price(country, account, link):
    price = None
    headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36',
        }
    # PT
    if country == 'PT':
        # Worten
        if account == 'WRT': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content)
            ele = parsed_html.find('span', {'class': 'w-product__price__current'})
            print(ele)
            if ele:
                text = ele[0].get_text()
                text = ''.join(text.split())
                price = text[:-1].replace('.', '').replace(',', '.')
                price = round(float(price)*100)
    # ES
    if country == 'ES':
        # Amazon 
        if account == 'AMZ':
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            # Deal price
            deal_block = parsed_html.find('span', {"id": "priceblock_dealprice"})
            if deal_block:
                ele = deal_block
            else:
                ele = parsed_html.find('span', {"id": "priceblock_ourprice"})
            if ele:
                text = ele.get_text()
                price = text[4:].replace('.', '').replace(',', '.')
                price = round(float(price)*100)
        # PCC
        if account == 'PCC': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            ele = parsed_html.select('.precioMain.h1')
            if ele:
                text = ele[0].get_text()
                text = ''.join(text.split())
                price = text[:-1].replace('.', '').replace(',', '.')
                price = round(float(price)*100)
        # MM
        if account == 'MM': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            ele = parsed_html.select('#priceBlock')
            if ele:
                price = ele[0].get_text().strip()
                price = round(float(price)*100)
        # XM
        if account == 'XM': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            ele = parsed_html.find(itemprop="price")
            if ele:
                price = ele.get("content")
                price = round(float(price)*100)
        # FNAC
        if account == 'FNAC': 
            response = requests.get(link, headers=headers, timeout=60)
            pattern = re.compile('product_discount_ati: "([0-9]*\.[0-9]*)"')
            price = pattern.findall(response.text)[0]
            price = round(float(price)*100)
        # CF
        if account == 'CF': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            ele = parsed_html.find('div', {"class": "price-info"})
            ele = ele.find('span', {"class": "price"})
            if ele:
                price = ele.get_text()
                price = price[:-2].replace('.', '').replace(',', '.')
                price = round(float(price)*100)
    # DE
    if country == 'DE': 
        # AMZ 
        if account == 'AMZ':
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
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
        # MM
        if account == 'MM': 
            response = requests.get(link, headers=headers, timeout=60)
            parsed_html = BeautifulSoup(response.content, 'html5lib')
            ele = parsed_html.find('meta', {"property": "product:price:amount"})
            if ele:
                price = ele['content']
                price = round(float(price)*100)
    return price

if __name__ == "__main__":
    print(query_online_price('DE', 'AMZ', 'https://www.amazon.de/dp/B071DP327B/'))
