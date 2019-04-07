from crm_config import *

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, desc, func
from database_setup import Base, User, EmailSubscription

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import time

from flask import render_template
from tplinkcrm import get_price_info
from jinja2 import Environment, PackageLoader, select_autoescape

print('Price monitor module started')

# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

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

template = env.get_template('price_report_email.html')

result = session.query(
        User
    ).filter(
        EmailSubscription.user_id == User.id, 
        EmailSubscription.email_list == 'price', 
    ).all()

for user in result:
    recipient = [user.email]
    subject = '%s price report available'%time.strftime('%y%m%d')
    accounts, low_flag, df = get_price_info(user.country)
    body = template.render(
            user_name = user.name, 
            accounts = accounts, 
            df_low = df[low_flag].sort_values(by='gap'), 
            df = df,
        )
    send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, subject, body)
print('Email sent')
