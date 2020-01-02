from crm_config import *

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, desc, func
from database_setup import Base, PriceLink, PriceHistory

import requests
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller
from fake_headers import Headers

import random, time
from datetime import datetime, timedelta
import argparse

import re

from price_util import query_online_price

# The script will be triggered by Cron, add random delay to make it's irregular
wait = random.uniform(0, 2*60*60)
time.sleep(wait)
print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

error_count = 0
try_left = 100

parser = argparse.ArgumentParser()
parser.add_argument('account')
parser.add_argument('country')
args = parser.parse_args()
account = args.account
country = args.country


# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

result = session.query(
        PriceHistory
    ).filter(
        datetime.now() - PriceHistory.timestamp < timedelta(seconds=43200)
    )
links_with_record = [e.price_link_id for e in result]

price_links = session.query(
        PriceLink
    ).filter(
        PriceLink.account == account, 
        PriceLink.country == country, 
    ).all()

fake_header = Headers(headers=True).generate()
proxies = None
for price_link in price_links:
    price = None
    if price_link.id in links_with_record:
        print('Link already with record, skip. {}'.format(price_link.link))
        continue
    wait = random.uniform(1, 10)
    time.sleep(wait)
    if try_left <= 0:
        print('Max try count reached, quit program')
        quit()
    account = price_link.account
    link = price_link.link
    price = query_online_price(country, account, link, fake_header, proxies)
    if price:
        error_count = 0
        newRow = PriceHistory(
            timestamp = datetime.now(), 
            price_link_id = price_link.id, 
            price = price, 
        )
        session.add(newRow)
        session.commit()
        continue
    if price_link.product.lep and price_link.product.lep!=0:
        print('Key product {}, add into queue again'.format(price_link.product.sku))
        price_links.append(price_link)
    if error_count < 3:
        error_count += 1
        continue
    else:
        # possible be blocked
        print('Error detected, use new user agent and new IP')
        error_count += 1
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
        if error_count > 10:
            print("Possible block by {} was detected, quit program".format(account))
            quit()

