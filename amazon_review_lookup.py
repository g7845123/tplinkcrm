from crm_config import *

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, desc, func
from database_setup import Base, Product, AmazonReview

import requests
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller
from fake_headers import Headers

import random, time
from datetime import datetime, timedelta
from dateparser.search import search_dates
import argparse

import re
import os

# The script will be triggered by Cron, add random delay to make it's irregular
# wait = random.uniform(0, 2*60*60)
wait = 0
time.sleep(wait)
print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

try_left = 100

parser = argparse.ArgumentParser()
parser.add_argument('country')
args = parser.parse_args()
country = args.country

# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

products = session.query(
        Product
    ).filter(
        Product.focused == True
    )
review_url_template = 'https://www.amazon.{}/product-reviews/{}/?formatType=current_format&pageNumber={}&sortBy=recent'

fake_header = Headers(headers=True).generate()
print('User agent: {}'.format(fake_header))
proxies = {
    'http': 'socks5://127.0.0.1:9050',
    'https': 'socks5://127.0.0.1:9050'
}
new_ip = requests.get('https://ident.me', proxies=proxies).text
print('IP: {}'.format(new_ip))

def get_html_to_parse(debug=False):
    if debug:
        # Will use cached file with priority
        cachefile_name = '{}_review.html'.format(product.asin)
        if cachefile_name in os.listdir('scraper_cache'):
            print('DEBUG: {} {}'.format(product.sku, product.asin))
            cachefile = open('scraper_cache/{}_review.html'.format(product.asin), 'rb')
            return True, cachefile.read()
    # Production mode or no cached file
    review_url = review_url_template.format(country, product.asin, page_num)
    response = requests.get(review_url, headers=fake_header, proxies=proxies, timeout=60)
    outfile = open('scraper_cache/{}_review.html'.format(product.asin), 'wb')
    outfile.write(response.content)
    print('{} page {}: {} {}'.format(product.sku, page_num, review_url, response.status_code))
    time.sleep(wait)
    return False, response.content

def html_stop_check(html_to_parse):
    if country == 'DE':
        if 'Leider stimmen' in str(html_to_parse):
            print('Page number {} exceeds limit, exit'.format(page_num))
            return 'break'
        if '0,0 von 5 Sternen' in str(html_to_parse):
            print('New product without review')
            return 'break'
        if 'Suchen Sie etwas' in str(html_to_parse):
            print('Invalid url, skip')
            return 'break'
        return 'continue'

def parse_review(review_div):
    if country == 'DE':
        if "Rezension aus Deutschland" not in review_div.text:
            print("Not German review, skip")
            return
        # As star can only be integer, only extract the interger part
        star = re.search('([0-9]),[0-9] von 5 Sternen', review_div.get_text())
        star = star.group(1)
        # star = star.replace(',', '.')
        star = int(star)
        review_id = review_div['id']
        review_title = review_div.find('a', {'data-hook': 'review-title'}).span.text
        review_body = review_div.find('span', {'data-hook': 'review-body'}).span.text
        review_date = review_div.find('span', {'data-hook': 'review-date'}).text
        review_date = search_dates(review_date, languages=[country.lower()])[0][1]
        return star, review_id, review_title, review_body, review_date

for product in products:
    debug = False
    # debug = True
    page_num = 1
    dup_review = 0
    print('===== Fetching review of {} ====='.format(product.sku))
    while True:
        if try_left <= 0:
            print('Blocked by Amazon with max try count reached, quit program')
            quit()
        if dup_review >=10:
            print('{} reviews already existed, skip to next product'.format(dup_review))
            break
        wait = random.uniform(1, 10)
        debug, html_to_parse = get_html_to_parse(debug=debug)
        if debug:
            print('Debug mode')
        else:
            print('Production mode')
        if html_stop_check(html_to_parse) != 'continue':
            break
        parsed_html = BeautifulSoup(html_to_parse, 'html5lib')
        review_divs = parsed_html.find_all('div', {'data-hook': 'review'})
        if not review_divs:
            # possible be blocked
            print('Error detected, use new user agent and new IP')
            try_left -= 1
            fake_header = Headers(headers=True).generate()
            print('New user agent: {}'.format(fake_header))
            with Controller.from_port(port = 9051) as c:
                c.authenticate()
                c.signal(Signal.NEWNYM)
            proxies = {
                'http': 'socks5://127.0.0.1:9050',
                'https': 'socks5://127.0.0.1:9050'
            }
            new_ip = requests.get('https://ident.me', proxies=proxies).text
            print('New IP: {}'.format(new_ip))
            continue
        # Get data as expected
        try_left = 100
        page_num += 1
        for review_div in review_divs:
            result = parse_review(review_div)
            if not result:
                break
            star, review_id, review_title, review_body, review_date = result
            if debug:
                print(star, review_id, review_title, review_body, review_date)
                continue
            result = session.query(
                    AmazonReview
                ).filter(
                    AmazonReview.amazon_id == review_id
                )
            if result.count():
                print('Review {} already existed, skip'.format(review_id))
                dup_review += 1
                continue
            newRow = AmazonReview(
                    product_id = product.id, 
                    amazon_id = review_id, 
                    date = review_date.date(), 
                    star = star, 
                    title = review_title, 
                    content = review_body, 
                    country = country, 
                    reported = False, 
                )
            session.add(newRow)
            session.commit()
            print('Review {} recorded'.format(review_id))
        if debug:
            # continue with next product
            break
