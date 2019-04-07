from crm_config import *

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, desc, func
from database_setup import Base, PriceLink, PriceHistory

import requests
from bs4 import BeautifulSoup

import time
from datetime import datetime
import argparse

import re

from price_util import query_online_price

parser = argparse.ArgumentParser()
parser.add_argument('account')
parser.add_argument('country')
args = parser.parse_args()
account = args.account
country = args.country

start_time = time.time()

# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

price_links = session.query(
        PriceLink
    ).filter(
        PriceLink.account == account, 
        PriceLink.country == country, 
    ).order_by(
        PriceLink.product_id
    ).all()
    # .filter(
    #     Product.id == PriceLink.product_id, 
    #     PriceLink.account == 'MM', 
    # ).all()

for price_link in price_links:
    account = price_link.account
    link = price_link.link
    try: 
        price = query_online_price(country, account, link)
    except:
        price = None
    if price == None:
        continue
    newRow = PriceHistory(
        timestamp = datetime.utcnow(), 
        price_link_id = price_link.id, 
        price = price, 
    )
    session.add(newRow)
    session.commit()
    time.sleep(1)

