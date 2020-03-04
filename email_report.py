from crm_config import *

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, desc, func
from database_setup import Base, User, EmailSubscription, Product, AmazonReview

import pandas as pd

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import time
from datetime import datetime, timedelta, date

from flask import render_template
from tplinkcrm import get_price_info, get_amazon_review_info
from jinja2 import Environment, PackageLoader, select_autoescape

import argparse


# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

# Get email type to send
parser = argparse.ArgumentParser()
parser.add_argument('report_type')
args = parser.parse_args()
report_type = args.report_type

print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

def send_email(sender, pwd, recipient, subject, body, files=None):
    # Import the email modules we'll need
    msg = MIMEMultipart('alternative')

    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ','.join(recipient)
    part1 = MIMEText(body, 'html')
    msg.attach(part1)

    s = smtplib.SMTP_SSL(SMTP_DOMAIN, SMTP_PORT)
    s.login(sender, pwd)
    s.sendmail(sender, recipient, msg.as_string())
    s.quit()

env = Environment(
    loader=PackageLoader('tplinkcrm', 'templates'),
    autoescape=select_autoescape(['html', 'xml'])
)

if report_type.upper() == 'PRICE':
    template = env.get_template('price_report_email.html')
    users = session.query(
            User
        ).filter(
            EmailSubscription.user_id == User.id, 
            EmailSubscription.email_list == 'price', 
        ).all()
    price_dict = {}
    # Avoid duplicated query by caching result by country
    for user in users:
        if user.country in price_dict:
            continue
        accounts, low_flag, df = get_price_info(user.country)
        price_dict[user.country] = accounts, low_flag, df
    for user in users:
        recipient = [user.email]
        subject = '%s price report available'%time.strftime('%y%m%d')
        body = template.render(
                user_name = user.name, 
                accounts = accounts, 
                df_low = df[low_flag], 
                df = df,
            )
        send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, subject, body)
        print('Price email sent to {}'.format(user.email))

if report_type.upper() == 'REVIEW':
    users = session.query(
            User
        ).filter(
            EmailSubscription.user_id == User.id, 
            EmailSubscription.email_list == 'review', 
        )
    review_dict = {}
    yesterday = date.today() - timedelta(days=1)
    for user in users:
        if user.country in review_dict:
            continue
        star_df, last_day = get_amazon_review_info(user.country)
        result = session.query(
                Product.id.label('product_id'), 
                Product.sku.label('sku'), 
            )
        product_df = pd.read_sql(result.statement, result.session.bind)
        result = session.query(
                AmazonReview.date.label('date'), 
                AmazonReview.country.label('country'), 
                AmazonReview.amazon_id.label('amazon_id'), 
                AmazonReview.title.label('title'), 
                AmazonReview.content.label('content'), 
                AmazonReview.star.label('star'), 
                AmazonReview.product_id.label('product_id')
            ).filter(
                AmazonReview.reported == False, 
            ).order_by(
                AmazonReview.date.desc()
            )
        review_detail_df = pd.read_sql(result.statement, result.session.bind)
        review_detail_df = review_detail_df.merge(product_df, on='product_id', how='left')
        review_detail_df.sort_values(by='star', ascending=True, inplace=True) 
        review_dict[user.country] = (star_df, last_day, review_detail_df)
    for user in users:
        recipient = [user.email]
        subject = '%s Amazon review report available'%time.strftime('%y%m%d')
        star_df, last_day, review_detail_df = review_dict[user.country]
        if review_detail_df.shape[0] == 0:
            print("All reviews were reported for {}".format(user.country))
            continue
        template = env.get_template('review_report_email.html')
        body = template.render(
                user_name = user.name, 
                star_df = star_df, 
                review_detail_df = review_detail_df,
                first_day = last_day - timedelta(days=365), 
                last_day = last_day, 
                country = user.country, 
            )
        send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, subject, body)
        print('Amazon review email sent to {}'.format(user.email))
    # Change reported to True
    result = session.query(
            AmazonReview
        ).filter(
            AmazonReview.reported == False
        )
    for review in result:
        review.reported = True
        session.add(review)
        session.commit()
    print('Changed Amazon review to reported')

