from crm_config import *

from flask import Flask, render_template, flash, redirect, url_for
from flask import session as login_session
from flask import request
from flask import escape
from flask import send_file

from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import label
from sqlalchemy import extract
from sqlalchemy.orm import aliased
from sqlalchemy import and_, or_
from database_setup import Base, User, Product, SkuToProduct, Stock, Sellout, Productline, PriceLink, PriceHistory, Task, ResetPwdToken, Account, NameToAccount, Sellin, Role, EmailSubscription, PackingListDetail, AccountNote, AccountContact, AccountPartner

import requests
from bs4 import BeautifulSoup
import re
from price_util import query_online_price

import numpy as np
import pandas as pd

from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView

import simplejson
from auth_util import valid_email, valid_password, make_salt, make_pw_hash

from datetime import datetime, timedelta, date
import time
from calendar import monthrange

from functools import wraps

# Email module
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email_template import *



app = Flask(__name__)

# Connect to Database and create database session
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.bind = engine
 
DBSession = sessionmaker(bind=engine)
session = DBSession()

# Login required decorator
def login_required(roles):
    def role_decorator(f):
        @wraps(f)
        def fun_wrapper(*args, **kwargs):
            if 'roles' not in login_session:
                flash('Please login to perform this operation')
                return redirect(url_for('login', redirecturl = request.path, **request.args))
            elif [e for e in roles if e in login_session['roles']]:
                return f(*args, **kwargs)
            flash('Your need to be verified before using this function, please contact james.guo@tp-link.com for details')
            return redirect(url_for('login', redirecturl = request.path, **request.args))
        return fun_wrapper
    return role_decorator


def getUserById(id):
    user = session.query(User).filter(User.id==id).first()
    if user:
        return user
    else:
        return None

class PriceRecord(object):

    def __init__(self, price, link):
        self.price = price
        self.link = link

    def __repr__(self):
        return str(self.price)

    def __lt__(self, other):
        return self.price < other.price

    def __mul__(self, other):
        return PriceRecord(int(self.price * other), self.link)

    def __truediv__(self, other):
        return PriceRecord(self.price / other, self.link)

    def __add__(self, other):
        return PriceRecord(self.price + other, self.link)

def send_email(user, pwd, recipient, subject, body, cc=None, files=None):
    msg = MIMEMultipart('alternative')

    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = ','.join(recipient)
    if cc:
        msg['Cc'] = cc
    part1 = MIMEText(body, 'html')
    msg.attach(part1)

    s = smtplib.SMTP_SSL(SMTP_DOMAIN, SMTP_PORT)
    s.login(user, pwd)
    if cc:
        s.sendmail(user, [recipient, cc], msg.as_string())
    else:
        s.sendmail(user, recipient, msg.as_string())
    s.quit()

def parsing_date(x):
    if type(x) in [pd.Timestamp, datetime]:
        return x
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(x, fmt)
        except:
            pass
    return None

def get_unmapped_account(account_names, country):
    result = session.query(
            NameToAccount
        ).filter(
            NameToAccount.country == country, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    name_col = result_df['name']
    unmapped_accounts = account_names[~account_names.isin(name_col)]
    return unmapped_accounts

def get_unmapped_sku(skus):
    result = session.query(
            SkuToProduct
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    sku_col = result_df['sku']
    unmapped_skus = skus[~skus.isin(sku_col)]
    return unmapped_skus

@app.route('/')
@app.route('/tplink')
def mainPage():
    email = login_session.get('email')
    accounts = session.query(
            Account
        )
    products = session.query(
            Product
        )
    managers = session.query(
            User
        ).filter(
            User.id == Account.user_id
        )
    result = session.query(
            Sellin
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    if 'email' not in login_session: 
        return redirect(url_for('login', redirecturl = '/'))
    user = getUserById(login_session['id'])
    return render_template(
        'index.html', 
        managers = managers, 
        login = login_session, 
        report_range = report_range, 
        country = user.country, 
    )

@app.route('/amazon-sellout/dashboard')
@login_required(['DE'])
def amazonSelloutDashboard():
    user = getUserById(login_session['id'])
    result = session.query(
            Account
        ).filter(
            Account.name == 'AMAZON {}'.format(user.country)
        ).first()
    amazon_id = result.id
    result = session.query(
            Product.sku.label('sku'), 
            Product.id.label('product_id'), 
        )
    sellout_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            Sellout.product_id.label('product_id'), 
            Sellout.date.label('date'), 
            Sellout.qty.label('qty'), 
        ).filter(
            Sellout.account_id == amazon_id
        ).order_by(
            Sellout.date.desc()
        )
    last_day = result.first().date
    # last 90 day
    first_day = last_day - timedelta(days=90)
    result = result.filter(
            Sellout.date > first_day, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df = result_df.groupby('product_id').sum()
    result_df['qty'] = result_df['qty'] / 90 * 30
    result_df['qty'] = result_df['qty'].astype(int)
    result_df.rename(columns = {
            'qty': 'd90', 
        }, inplace = True)
    sellout_df = sellout_df.merge(result_df, on='product_id', how='left')
    # remove product without sellout
    sellout_df = sellout_df[~sellout_df['d90'].isna()]
    # last 30 day
    first_day = last_day - timedelta(days=30)
    result = result.filter(
            Sellout.date > first_day, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df = result_df.groupby('product_id').sum()
    result_df['qty'] = result_df['qty'].astype(int)
    result_df.rename(columns = {
            'qty': 'd30', 
        }, inplace = True)
    sellout_df = sellout_df.merge(result_df, on='product_id', how='left')
    # last 7 day
    first_day = last_day - timedelta(days=7)
    result = result.filter(
            Sellout.date > first_day, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df = result_df.groupby('product_id').sum()
    result_df['qty'] = result_df['qty'] / 7 * 30
    result_df['qty'] = result_df['qty'].astype(int)
    result_df.rename(columns = {
            'qty': 'd7', 
        }, inplace = True)
    sellout_df = sellout_df.merge(result_df, on='product_id', how='left')
    # Sort by D7-D90 Gap
    sellout_df['weight'] = abs(sellout_df['d7']-sellout_df['d90'])
    sellout_df.sort_values(by='weight', ascending=False, inplace=True) 
    return render_template(
        'amazon_sellout_dashboard.html', 
        login = login_session, 
        country = user.country, 
        sellout_df = sellout_df, 
        last_day = last_day, 
    )

@app.route('/amazon-sellout/detail')
@login_required(['DE'])
def amazonSelloutDetail():
    user = getUserById(login_session['id'])
    product_id = request.args.get('product')
    result = session.query(
            Account
        ).filter(
            Account.name == 'AMAZON {}'.format(user.country)
        ).first()
    amazon_id = result.id
    result = session.query(
            Product
        ).filter(
            Product.id == product_id
        ).first()
    sku = result.sku
    result = session.query(
            Sellout.date.label('date'), 
            func.sum(Sellout.qty).label('qty'), 
        ).filter(
            Sellout.product_id == product_id, 
            Sellout.account_id == amazon_id, 
        ).group_by(
            'date'
        ).order_by(
            'date'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['date'] = pd.to_datetime(result_df['date'])
    result_df.set_index('date', inplace=True)
    # D1 Sellout
    d1_sellout_df = result_df
    row_count = result_df.shape[0]
    # D7 Sellout
    d7_sellout_df = result_df[row_count%7:].resample('7D').sum()
    # D30 Sellout
    d30_sellout_df = result_df[row_count%30:].resample('30D').sum()
    return render_template(
        'amazon_sellout_detail.html', 
        login = login_session, 
        country = user.country, 
        sku = sku, 
        d1_sellout_df = d1_sellout_df, 
        d7_sellout_df = d7_sellout_df, 
        d30_sellout_df = d30_sellout_df, 
    )

@app.route('/operational/dashboard')
@login_required(['ES', 'DE'])
def operationalDashboard():
    urgent_pls = session.query(
            PackingListDetail, 
        ).filter(
            or_(
                PackingListDetail.shipped_date == None, 
                PackingListDetail.invoice_date == None, 
            ), 
            PackingListDetail.required_date != None, 
        ).all()
    open_pls = session.query(
            PackingListDetail, 
        ).filter(
            or_(
                PackingListDetail.shipped_date == None, 
                PackingListDetail.invoice_date == None, 
            )
        ).order_by(
            PackingListDetail.pl_date
        ).all()
    closed_pls = session.query(
            PackingListDetail, 
        ).filter(
            PackingListDetail.shipped_date != None, 
            PackingListDetail.invoice_date != None, 
        ).order_by(
            PackingListDetail.pl_date
        ).all()
    return render_template(
        'operational_dashboard.html', 
        login = login_session, 
        urgent_pls = urgent_pls, 
        open_pls = open_pls, 
        closed_pls = closed_pls, 
    )

@app.route('/operational/pl-upload-check', methods=['POST'])
@login_required(['admin'])
def plUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    pl_file = request.files.get('pl-file')
    pl_df = pd.read_excel(pl_file)
    # Check whether header in submission is ok
    required_header = pd.Series(['Customer', 'PI', 'SO', 'PL Date', 'Ready Date', 'Shipped Date', 'Invoice Date', 'Required Date', 'Urgent'])
    header_err = required_header[~required_header.isin(pl_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    # Check whether date in submission is ok
    date_col = pl_df['PL Date'].apply(parsing_date)
    date_err = date_col[pd.isnull(date_col)]
    if not date_err.empty: 
        response['date'] = date_err.to_dict()
        return simplejson.dumps(response, default=str)
    else:
        response['date'] = 'pass'
    return simplejson.dumps(response)

@app.route('/operational/pl-upload', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def plUpload():
    if request.method == 'GET':
        return render_template(
            'operational_pl_upload.html', 
            login = login_session, 
        )
    pl_file = request.files.get('pl-file')
    pl_df = pd.read_excel(pl_file)
    # Check whether header in submission is ok
    pl_df['Customer'] = pl_df['Customer'].apply(lambda x: x.strip().upper())
    pl_df['SO'] = pl_df['SO'].apply(lambda x: str(x).strip().upper())
    pl_df['PL Date'] = pl_df['PL Date'].apply(parsing_date)
    pl_df['Ready Date'] = pl_df['Ready Date'].apply(parsing_date)
    pl_df['Invoice Date'] = pl_df['Invoice Date'].apply(parsing_date)
    pl_df['Shipped Date'] = pl_df['Shipped Date'].apply(parsing_date)
    pl_df['Required Date'] = pl_df['Required Date'].apply(parsing_date)
    for idx, row in pl_df.iterrows():
        if not row['SO']:
            continue
        pl = session.query(
                PackingListDetail
            ).filter(
                PackingListDetail.so == row['SO']
            ).first()
        # Basic information can't be changed once uploaded
        if not pl:
            pl = PackingListDetail()
            pl.customer = row['Customer']
            pl.pi = row['PI']
            pl.so = row['SO']
            pl.pl_date = row['PL Date']
        if not pd.isnull(row['Ready Date']):
            pl.ready_date = row['Ready Date']
        if not pd.isnull(row['Shipped Date']):
            pl.shipped_date = row['Shipped Date']
        if not pd.isnull(row['Invoice Date']):
            pl.invoice_date = row['Invoice Date']
        if not pd.isnull(row['Required Date']):
            pl.required_date = row['Required Date']
        session.add(pl)
        session.commit()
    flash('Successfully uploaded')
    return redirect('/operational/dashboard')

@app.route('/operational/dailypi-upload', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def dailypiUpload():
    if request.method == 'GET':
        return render_template(
            'operational_dailypi_upload.html', 
            login = login_session, 
        )
    pl_file = request.files.get('dailypi-file')
    pl_df = pd.read_excel(pl_file, converters={'Tracking Number': str})
    # pl_df = pl_df.fillna('')
    # Check whether header in submission is ok
    # required_header = pd.Series(['Related Order No.', 'D/L', 'Shipped Date', 'Tracking Number'])
    # header_err = required_header[~required_header.isin(pl_df.columns)]
    pl_df['D/L'] = pl_df['D/L'].apply(parsing_date)
    pl_df['Shipped Date'] = pl_df['Shipped Date'].apply(parsing_date)
    for idx, row in pl_df.iterrows():
        sos = str(row['Related Order No.']).strip()
        if not sos:
            continue
        sos = sos.split('/')
        for so in sos:
            so = so.strip()
            pl = session.query(
                    PackingListDetail
                ).filter(
                    PackingListDetail.so == so
                ).first()
            if not pl:
                continue
            if not pl.ready_date and not pd.isnull(row['D/L']):
                pl.ready_date = row['D/L']
            if not pl.shipped_date and not pd.isnull(row['Shipped Date']):
                pl.shipped_date = row['Shipped Date']
            if not pl.pl_name and not pd.isnull(row['Tracking Number']):
                pl.pl_name = row['Tracking Number']
            session.add(pl)
            session.commit()
    flash('Successfully uploaded')
    return redirect('/operational/dashboard')

@app.route('/operational/invoice-upload', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def invoiceUpload():
    if request.method == 'GET':
        return render_template(
            'operational_invoice_upload.html', 
            login = login_session, 
        )
    invoice_file = request.files.get('invoice-file')
    pl_df = pd.read_excel(invoice_file, converters={'Tracking Number': str, 'Delivery NO.': str})
    pl_df['Date'] = pl_df['Date'].apply(parsing_date)
    pl_df = pl_df[~pd.isnull(pl_df['Delivery NO.'])]
    pl_df['SO'] = pl_df['Delivery NO.'].apply(lambda x: str(x).strip().upper())
    for idx, row in pl_df.iterrows():
        pl = session.query(
                PackingListDetail
            ).filter(
                PackingListDetail.so == row['SO']
            ).first()
        if not pl:
            continue
        if not pl.invoice_date:
            pl.invoice_date = row['Date']
        session.add(pl)
        session.commit()
    flash('Successfully uploaded')
    return redirect('/operational/dashboard')

@app.route('/interaction/dashboard')
@login_required(['admin', 'manager'])
def interactionDashboard():
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_start = parsing_date(date_start)
    date_end = request.args.get('end')
    date_end = parsing_date(date_end)
    interactions = session.query(
            AccountNote.id.label('id'), 
            AccountNote.created.label('date'), 
            Account.name.label('account'), 
            User.name.label('manager'), 
            AccountNote.type.label('type'), 
            AccountNote.note.label('note'), 
        ).filter(
            AccountNote.account_id == Account.id, 
            AccountNote.user_id == User.id, 
            AccountNote.type.in_(['CALL', 'MEETING', 'EMAIL']), 
            AccountNote.created >= date_start, 
            AccountNote.created <= date_end, 
            User.country == user.country, 
        ).order_by(
            AccountNote.created.desc(),     
        )
    return render_template(
        'interaction_dashboard.html', 
        login = login_session, 
        interactions = interactions, 
    )

# @app.route('/operational/download')
# def plDetailDownload():
#     open_pls = session.query(
#             PackingListDetail, 
#         ).filter(
#             PackingListDetail.invoice_date == None, 
#         ).order_by(
#             PackingListDetail.pl_date
#         ).all()
#     column_names = ['customer', 'pi', 'so', 'pl_date', 'promised_date', 'ready_date', 'invoice_date', 'truck_id', 'required_date']
#     return excel.make_response_from_query_sets(open_pls, column_names, "xlsx")

@app.route('/account/query')
@login_required(['ES', 'DE'])
def queryAccount():
    user = getUserById(login_session['id'])
    account_name = request.args.get('name')
    rows = session.query(
            Account
        ).filter(
            Account.name.ilike('%'+account_name+'%'), 
            Account.country == user.country, 
        )
    results = [
            {
                'id': row.id, 
                'text': row.name, 
            } for row in rows
        ]
    return simplejson.dumps(results)

@app.route('/account/merge', methods=['GET', 'POST'])
@login_required(['admin'])
def mergeAccount():
    accounts = session.query(
            Account
        )
    if request.method == 'GET':
        return render_template(
            'account_merge.html', 
            login = login_session, 
        )
    else: 
        main_account_id = request.form.get('main-account')
        account_to_merge_id = request.form.get('account-to-merge')
        # Sellout
        rows = session.query(
                Sellout
            ).filter(
                Sellout.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Stock
        rows = session.query(
                Stock
            ).filter(
                Stock.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Sellin account
        rows = session.query(
                Sellin
            ).filter(
                Sellin.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Sellin distri
        rows = session.query(
                Sellin
            ).filter(
                Sellin.distri_id == account_to_merge_id
            )
        for row in rows:
            row.distri = main_account_id
            session.add(row)
        # Name to account
        rows = session.query(
                NameToAccount
            ).filter(
                NameToAccount.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Account note
        rows = session.query(
                AccountNote
            ).filter(
                AccountNote.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Account contact
        rows = session.query(
                AccountContact
            ).filter(
                AccountContact.account_id == account_to_merge_id
            )
        for row in rows:
            row.account_id = main_account_id
            session.add(row)
        # Delete account
        session.query(
                Account
            ).filter(
                Account.id == account_to_merge_id
            ).delete(
                synchronize_session='fetch'
            )
        session.commit()
        flash('Successfully merged')
        return render_template(
            'account_merge.html', 
            login = login_session, 
        )

# @app.route('/task/dashboard')
# @login_required(['ES'])
# def taskDashboard():
#     tasks_from_me = session.query(
#         Task, 
#     ).filter(
#         Task.task_from_id == login_session['id'], 
#         Task.status != 'Completed', 
#     ).order_by(
#         Task.deadline
#     )
#     tasks_to_me = session.query(
#         Task, 
#     ).filter(
#         Task.task_to_id == login_session['id'], 
#         Task.status != 'Completed', 
#     ).order_by(
#         Task.deadline
#     )
#     tasks_completed = session.query(
#         Task, 
#     ).filter(
#         Task.task_to_id == login_session['id'], 
#         Task.status == 'Completed', 
#     ).order_by(
#         Task.deadline
#     )
#     return render_template(
#         'task_dashboard.html', 
#         login = login_session, 
#         tasks_from_me = tasks_from_me, 
#         tasks_to_me = tasks_to_me, 
#         tasks_completed = tasks_completed, 
#     )

# @app.route('/task/delete/<int:task_id>')
# @login_required(['ES'])
# def deleteTask(task_id):
#     task = session.query(
#         Task, 
#     ).filter(
#         Task.id == task_id, 
#     )
#     if not task:
#         flash('Task not found')
#         return redirect('/task/dashboard')
#     task = task.one()
#     # User is the task owner
#     if login_session['id'] == task.task_from_id:
#         session.delete(task)
#         session.commit()
#         flash('Task deleted')
#         return redirect('/task/dashboard')
#     # User is not the task owner
#     else:
#         flash('Only task owner can delete the task')
#         return redirect('/task/edit/%s'%task_id)

# @app.route('/task/edit/<int:task_id>', methods=['GET', 'POST'])
# @login_required(['ES'])
# def editTask(task_id):
#     task = session.query(
#         Task, 
#     ).filter(
#         Task.id == task_id, 
#     )
#     if not task:
#         flash('Task not found')
#         return redirect('/task/dashboard')
#     task = task.one()
#     # User is the task owner
#     if login_session['id'] == task.task_from_id:
#         if request.method == 'GET':
#             return render_template(
#                 'task_edit_owner.html', 
#                 login = login_session, 
#                 task = task, 
#             )
#         else:
#             if request.form.get('submit') == 'send-reminder':
#                 result = session.query(
#                         User
#                     ).filter(
#                         User.id == task.task_to_id, 
#                     ).first()
#                 recipient = [result.email]
#                 body = task_reminder_email.format(subject=task.subject, detail=task.detail, task_id=task.id)
#                 send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'Task reminder: {}'.format(task.subject), body, cc=login_session.get('email'))
#                 flash('Reminder sent by email')
#                 return redirect('/task/dashboard')
#             task.detail = request.form.get('detail')
#             task.subject = request.form.get('subject')
#             task.deadline = request.form.get('deadline')
#             email_notification = request.form.get('email-notification')
#             if request.form.get('submit') == 'mark-as-completed':
#                 task.status = 'Completed'
#             if request.form.get('submit') == 'rework-required':
#                 task.status = 'Rework Required'
#             flash('Changes saved')
#             session.add(task)
#             session.commit()
#             if email_notification == '1':
#                 result = session.query(
#                         User
#                     ).filter(
#                         User.id == task.task_to_id, 
#                     ).first()
#                 recipient = [e.email for e in result]
#                 body = task_status_email.format(subject=task.subject, status=task.status, detail=task.detail, task_id=task.id)
#                 send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'Task status change: {}'.format(task.subject), body, cc=login_session.get('email'))
#             return redirect('/task/dashboard')
#     # User is not the task owner
#     else:
#         if request.method == 'GET':
#             return render_template(
#                 'task_edit.html', 
#                 login = login_session, 
#                 task = task, 
#             )
#         else:
#             task.detail = request.form.get('detail')
#             status = request.form.get('status')
#             send_email_flag = True
#             if request.form.get('submit') == 'save-without-email' and status != "Completed":
#                 send_email_flag = False
#             if status:
#                 task.status = status
#             session.add(task)
#             session.commit()
#             flash('Changes saved')
#             if send_email_flag: 
#                 result = session.query(
#                         User
#                     ).filter(
#                         User.id == task.task_from_id, 
#                     ).first()
#                 recipient = [result.email]
#                 body = task_status_email.format(subject=task.subject, status=task.status, detail=task.detail, task_id=task.id)
#                 send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'Task status change: {}'.format(task.subject), body, cc=login_session.get('email'))
#                 flash('Email sent')
#             return redirect('/task/dashboard')

# @app.route('/task/create', methods=['GET', 'POST'])
# @login_required(['ES'])
# def createTask():
#     users = session.query(
#             User, 
#         ).all()
#     if request.method == 'GET':
#         return render_template(
#             'task_create.html', 
#             login = login_session, 
#             users = users, 
#         )
#     if request.method == 'POST':
#         subject = request.form.get('subject')
#         task_tos = request.form.getlist('to')
#         deadline = request.form.get('deadline')
#         deadline = datetime.strptime(deadline, "%Y-%m-%d").date()
#         detail = request.form.get('detail')
#         email_notification = request.form.get('email-notification')
#         for task_to in task_tos: 
#             new_task = Task(
#                 created = datetime.utcnow(), 
#                 subject = subject, 
#                 task_from_id = login_session['id'], 
#                 task_to_id = task_to, 
#                 deadline = deadline, 
#                 detail = detail, 
#                 status = 'To Do', 
#             )
#             session.add(new_task)
#             session.commit()
#             if email_notification == '1':
#                 result = session.query(
#                         User
#                     ).filter(
#                         User.id == task_to
#                     ).first()
#                 recipient = [result.email]
#                 body = task_create_email.format(subject=subject, detail=detail, task_id=new_task.id)
#                 send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'New task: {}'.format(subject), body, cc=login_session.get('email'))
#         flash('Task successfully created')
#         return redirect('/task/dashboard')

# @app.route('/sellout/download')
# @login_required(['ES'])
# def selloutDownload():
#     result = session.query(
#             Sellout.date.label('date'), 
#             Product.sku.label('sku'), 
#             Account.name.label('account'), 
#             Sellout.sellout.label('qty'), 
#             Sellout.total.label('revenue'), 
#         ).filter(
#             Product.id == Sellout.product_id, 
#             Account.id == Sellout.account_id, 
#             Sellout.sellout != 0, 
#         )
#     sellout_df = pd.read_sql(result.statement, result.session.bind)
#     sellout_df['revenue'] = sellout_df['revenue']/100
#     sellout_df['year'] = pd.DatetimeIndex(sellout_df['date']).year
#     sellout_df['month'] = pd.DatetimeIndex(sellout_df['date']).month
#     sellout_df.drop('date', axis=1, inplace=True)
#     g = sellout_df.groupby(['account', 'sku', 'year', 'month'])
#     sellout_df = g.aggregate({'qty': np.mean, 'revenue': np.mean}).reset_index()
#     sellout_df = sellout_df.set_index('sku')
#     sellout_df['revenue'] = (sellout_df['revenue']/7*30.5).astype(int)
#     sellout_df['qty'] = (sellout_df['qty']/7*30.5).astype(int)
#     result = session.query(
#             Product.sku.label('sku'), 
#             Product.eol.label('eol'), 
#             Productline.category.label('category'), 
#             Productline.pl_wide.label('pl_wide'), 
#             Productline.pl_narrow.label('pl_narrow'), 
#         ).filter(
#             Product.pl_id == Productline.id, 
#         )
#     pl_df = pd.read_sql(result.statement, result.session.bind)
#     pl_df = pl_df.set_index('sku')
#     sellout_df = sellout_df.join(pl_df)
#     writer = pd.ExcelWriter('/var/www/tplinkcrm/report/%s_report.xlsx'%login_session['id'], engine='xlsxwriter')
#     sellout_df.to_excel(writer)
#     writer.save()
#     return send_file('/var/www/tplinkcrm/report/%s_report.xlsx'%login_session['id'])

# @app.route('/sellin/download')
# @login_required(['admin'])
# def sellinDownload():
#     Distri_account = aliased(Account, name='distri_account')
#     Customer_account = aliased(Account, name='customer_account')
#     date_start = request.args.get('start')
#     date_start = parsing_date(date_start)
#     date_end = request.args.get('end')
#     date_end = parsing_date(date_end)
#     result = session.query(
#             Sellin.date.label('date'), 
#             Distri_account.name.label('distri'), 
#             Customer_account.name.label('customer'), 
#             Customer_account.type.label('type'), 
#             Product.sku.label('sku'), 
#             Sellin.distri_cost.label('distri_cost'), 
#             Productline.category.label('category'), 
#             Productline.pl_wide.label('pl_wide'), 
#             Productline.pl_narrow.label('pl_narrow'), 
#             Sellin.qty.label('qty'), 
#         ).filter(
#             Sellin.product_id == Product.id, 
#             Product.pl_id == Productline.id, 
#             Distri_account.id == Sellin.distri_id, 
#             Customer_account.id == Sellin.account_id, 
#             Sellin.date >= date_start, 
#             Sellin.date <= date_end, 
#         )
#     sellin_df = pd.read_sql(result.statement, result.session.bind)
#     sellin_df.distri_cost = sellin_df.distri_cost / 100
#     # writer = pd.ExcelWriter('/var/www/tplinkcrm/report/%s_report.xlsx'%login_session['id'], engine='xlsxwriter')
#     # sellin_df.to_excel(writer)
#     # writer.save()
#     sellin_df.to_csv('/var/www/tplinkcrm/report/%s_report.csv'%login_session['id'])
#     return send_file('/var/www/tplinkcrm/report/%s_report.csv'%login_session['id'])

def get_price_info(country):
    price_links = session.query(
            PriceLink
        ).filter(
            PriceLink.country == country
        ).all()
    results = []
    header = ['product_id', 'account', 'price']
    for price_link in price_links:
        result = session.query(
                PriceHistory.timestamp.label('time'), 
                Product.id.label('product_id'), 
                PriceLink.account.label('account'),  
                PriceHistory.price.label('price'), 
            ).filter(
                PriceLink.id == price_link.id, 
                Product.id == PriceLink.product_id, 
                PriceLink.id == PriceHistory.price_link_id, 
            ).order_by(
                PriceHistory.timestamp.desc(), 
            ).first()
        price_record = PriceRecord(
                price = result.price, 
                link = price_link.link, 
            )
        results.append([result.product_id, result.account, price_record])
    df = pd.DataFrame(results, columns=header)
    df = df.pivot(
            index='product_id', 
            columns='account', 
            values='price', 
        )
    df[df.isnull()] = PriceRecord(price=np.inf, link=None)
    accounts = list(df)
    df['min'] = df.apply(lambda row: min([e.price for e in row]), axis=1)
    result = session.query(
            Product.id.label('product_id'), 
            Product.sku.label('sku'), 
            Product.distri_cost.label('distri_cost'), 
            Product.msrp.label('msrp'), 
            Product.focused.label('focused'), 
        )
    product_df = pd.read_sql(result.statement, result.session.bind)
    product_df = product_df.set_index('product_id')
    df = df.join(product_df)
    df['diff'] = 1 - df['min']*1./df['msrp']
    if country == 'ES':
        tax_rate = ES_TAX_RATE
    if country == 'DE':
        tax_rate = DE_TAX_RATE
    df['gap'] = df['min']/tax_rate / df['distri_cost'] - 1
    low_flag = df['gap'] < LOWEST_CHANNEL_MARGIN
    return accounts, low_flag, df

@app.route('/price/dashboard')
@login_required(['ES', 'DE'])
def priceDashboard():
    product_id = request.args.get('product')
    user = getUserById(login_session['id'])
    accounts, low_flag, df = get_price_info(user.country)
    products = session.query(
            Product
        )
    if product_id == "all":
        return render_template(
            'price_all.html', 
            login = login_session, 
            accounts = accounts, 
            df = df,
        )
    try:
        product_id = int(product_id)
    except:
        product_id = 0
    return render_template(
        'price_dashboard.html', 
        login = login_session, 
        accounts = accounts, 
        df_low = df[low_flag].sort_values(by='gap'), 
        df = df,
        products = products, 
        product_id = product_id, 
    )

@app.route('/price/history')
@login_required(['ES', 'DE'])
def priceHistory():
    product_id = request.args.get('product')
    product = getProductById(product_id)
    user = getUserById(login_session['id'])
    rows = session.query(
            PriceLink.account.distinct().label('account'), 
        ).filter(
            PriceLink.country == user.country
        ).all()
    accounts = [row.account for row in rows]
    result_dict = {}
    for account in accounts:
        result = session.query(
                PriceHistory.timestamp.label('time'), 
                PriceHistory.price.label('price')
            ).join(
                PriceLink
            ).join(
                Product
            ).filter(
                PriceHistory.price_link_id == PriceLink.id, 
                PriceLink.product_id == Product.id, 
            ).filter(
                PriceLink.account == account, 
                Product.id == product_id, 
                PriceLink.country == user.country, 
            ).order_by(
                PriceHistory.timestamp, 
            ).all()
        result_dict[account] = result
    return render_template(
        'price_history.html', 
        login = login_session, 
        sku = product.sku, 
        accounts = accounts, 
        result_dict = result_dict,
    )

@app.route('/product/download')
@login_required(['ES', 'DE'])
def productDownload():
    result = session.query(
            SkuToProduct.sku.label('Raw SKU'), 
            Product.sku.label('SKU'), 
            Product.distri_cost.label('Distributor Price'), 
            Product.asin.label('ASIN'), 
            Product.ean.label('EAN'),
            Product.eol.label('EOL'), 
            Product.bu.label('BU'), 
            Product.category.label('Category'), 
            Product.sub_category.label('Sub-category'), 
        ).filter(
            SkuToProduct.product_id == Product.id
        ).order_by(
            SkuToProduct.sku
        )
    sku_df = pd.read_sql(result.statement, result.session.bind)
    writer = pd.ExcelWriter('/var/www/tplinkcrm/report/products.xlsx', engine='xlsxwriter')
    sku_df.to_excel(writer, index=False)
    writer.save()
    return send_file('/var/www/tplinkcrm/report/products.xlsx')

@app.route('/account/download')
@login_required(['ES', 'DE'])
def accountDownload():
    user = getUserById(login_session['id'])
    result = session.query(
            NameToAccount.name.label('Name'), 
            NameToAccount.account_id.label('Account ID'), 
        ).filter(
            NameToAccount.country == user.country, 
        )
    account_df = pd.read_sql(result.statement, result.session.bind)
    writer = pd.ExcelWriter('/var/www/tplinkcrm/report/accounts.xlsx', engine='xlsxwriter')
    account_df.to_excel(writer, index=False)
    writer.save()
    return send_file('/var/www/tplinkcrm/report/accounts.xlsx')

@app.route('/account-contact/download')
@login_required(['ES', 'DE'])
def accountContactDownload():
    user = getUserById(login_session['id'])
    result = session.query(
            Account.name, 
            AccountContact.name, 
            AccountContact.title, 
            AccountContact.email, 
            AccountContact.phone, 
        ).filter(
            Account.id == AccountContact.account_id, 
        )
    account_contact_df = pd.read_sql(result.statement, result.session.bind)
    writer = pd.ExcelWriter('/var/www/tplinkcrm/report/account_contacts.xlsx', engine='xlsxwriter')
    account_contact_df.to_excel(writer, index=False)
    writer.save()
    return send_file('/var/www/tplinkcrm/report/account_contacts.xlsx')

# @app.route('/woc/ka/download')
# @login_required(['ES'])
# def wocDownload():
#     args = request.args.to_dict()
#     args['redirecturl'] = request.path
#     account_id = request.args.get('account')
#     result = session.query(
#             Sellout.date.distinct().label('date')
#         ).filter(
#             Sellout.account_id == account_id, 
#         ).order_by(
#             Sellout.date.desc()
#         ).limit(8)
#     sellout_dates = [q.date for q in result.all()]
#     result = session.query(
#             Stock.date.distinct().label('date')
#         ).filter(
#             Stock.account_id == account_id, 
#         ).order_by(
#             Stock.date.desc()
#         ).first()
#     stock_date = result.date
#     # Sellout
#     result = session.query(
#             Product.sku.label('sku'), 
#             Product.distri_cost.label('distri_cost'), 
#             Product.focused.label('focused'), 
#             Product.eol.label('eol'), 
#         )
#     result_df = pd.read_sql(result.statement, result.session.bind)
#     # Add stock column
#     result = session.query(
#             Product.sku, 
#             func.sum(Stock.stock).label('stock'), 
#         ).filter(
#             Product.id == Stock.product_id, 
#             Stock.account_id == account_id, 
#             Stock.date == stock_date, 
#         ).group_by(
#             Product.sku
#         )
#     stock_df = pd.read_sql(result.statement, result.session.bind)
#     result_df = result_df.merge(stock_df, on='sku', how='left')
#     for date in sellout_dates:
#         result = session.query(
#                 Product.sku.label('sku'), 
#                 func.sum(Sellout.sellout).label(str(date)), 
#             ).filter(
#                 Sellout.account_id == account_id, 
#                 Sellout.date == date, 
#                 Sellout.product_id == Product.id, 
#             ).group_by(
#                 Product.sku
#             )
#         sellout_df = pd.read_sql(result.statement, result.session.bind)
#         result_df = result_df.merge(sellout_df, on='sku', how='left')
#     writer = pd.ExcelWriter('/var/www/tplinkcrm/report/woc_report_{}.xlsx'.format(login_session['id']), engine='xlsxwriter')
#     result_df.to_excel(writer)
#     writer.save()
#     return send_file('/var/www/tplinkcrm/report/woc_report_{}.xlsx'.format(login_session['id']), attachment_filename='woc_report.xlsx', as_attachment=True)

@app.route('/stock/dashboard')
@login_required(['ES', 'DE'])
def distriStockDashboard():
    result = session.query(
            Sellin
        ).order_by(
            Sellin.date.desc()
        ).first()
    user = getUserById(login_session['id'])
    # last_day = monthrange(result.date.year, result.date.month)[1]
    # report_day_end = date(result.date.year, result.date.month, last_day)
    report_day_end = result.date
    report_day_start = report_day_end - timedelta(days=90)
    # report_day_start = date(report_day_start.year, report_day_start.month, 1)
    result = session.query(
            Account.id.label('id'), 
            Account.name.label('account'), 
        ).filter(
            Account.type == 'DISTRIBUTOR', 
            Account.country == user.country, 
        )
    result_df = pd.read_sql(
            result.statement, 
            result.session.bind, 
            index_col = 'id', 
        )
    result = session.query(
            Sellin.distri_id.label('id'), 
            func.sum(Sellin.qty*Sellin.unit_price).label('sellin'), 
        ).filter(
            Sellin.date >= report_day_start, 
            Sellin.date <= report_day_end, 
        ).group_by(
            Sellin.distri_id
        )
    sellin_df = pd.read_sql(
            result.statement, 
            result.session.bind, 
            index_col = 'id', 
        )
    result_df = result_df.join(sellin_df)
    result = session.query(
            Stock.account_id.label('id'), 
            Stock.date.label('date'), 
            func.sum(Stock.stock*Stock.distri_cost).label('stock'), 
            func.sum(Stock.bo*Stock.distri_cost).label('bo'), 
        ).group_by(
            Stock.account_id, 
            Stock.date, 
        ).order_by(
            Stock.date.desc()
        )
    stock_df = pd.read_sql(
            result.statement, 
            result.session.bind, 
            index_col = 'id', 
        )
    stock_df = stock_df[~stock_df.index.duplicated(keep='first')]
    result_df = result_df.join(stock_df)
    result_df['woc'] = result_df['stock']*12/result_df['sellin']
    result_df['woc_with_bo'] = (result_df['stock'] + result_df['bo'])*12/result_df['sellin']
    result_df = result_df.fillna(0)
    result_df = result_df.sort_values(by=['stock'], ascending=False)
    return render_template(
        'distri_stock_dashboard.html', 
        login = login_session, 
        result_df = result_df, 
    )

@app.route('/stock/<int:account_id>')
@login_required(['ES', 'DE'])
def distriStock(account_id):
    woc_target = request.args.get('woc-target')
    woc_target = int(woc_target)
    account = session.query(
            Account
        ).filter(
            Account.id == account_id, 
        ).first() 
    result = session.query(
            Sellin.date.distinct().label('date')
        ).filter(
            Sellin.distri_id == account_id, 
        ).order_by(
            Sellin.date.desc()
        ).first() 
    sellin_recent_date = result.date
    result = session.query(
            Product.sku.label('sku'), 
            Product.distri_cost.label('distri_cost'), 
            Product.focused.label('focused'), 
            Product.eol.label('eol'), 
        )
    result_df = pd.read_sql( result.statement, 
            result.session.bind, 
            index_col = 'sku', 
        )
    date_cols = []
    for i in range(12):
        delta = timedelta(days=7*i)
        date_end = sellin_recent_date - delta
        date_start = date_end - timedelta(days=7)
        result = session.query(
                Product.sku.label('sku'), 
                func.sum(Sellin.qty).label('qty'), 
            ).filter(
                Product.id == Sellin.product_id, 
                Sellin.distri_id == account_id, 
                Sellin.date >= date_start, 
                Sellin.date < date_end, 
            ).group_by(
                'sku'
            )
        sellin_df = pd.read_sql(
                result.statement, 
                result.session.bind, 
                index_col = 'sku'
            )
        result_df[date_start] = sellin_df['qty']
        date_cols.append(date_start)
        # sellin_df = sellin_df.join(sellin_weekly_df)
    result = session.query(
            Stock.date.distinct().label('date')
        ).filter(
            Stock.account_id == account_id, 
        ).order_by(
            Stock.date.desc()
        ).first()
    stock_date = result.date
    # Add stock column
    result = session.query(
            Product.sku.label('sku'), 
            func.sum(Stock.stock).label('stock'), 
            func.sum(Stock.bo).label('bo'), 
        ).filter(
            Product.id == Stock.product_id, 
            Stock.account_id == account_id, 
            Stock.date == stock_date, 
        ).group_by(
            Product.sku
        )
    stock_df = pd.read_sql(
            result.statement, 
            result.session.bind, 
            index_col = 'sku', 
        )
    result_df['stock'] = stock_df['stock']
    result_df['bo'] = stock_df['bo']
    result_df['adj_weekly'] = 0
    result_df['woc'] = 0
    result_df['order_suggestion'] = 0
    result_df = result_df.fillna(0)
    for sku, row in result_df.iterrows():
        eight_week_sales = row[date_cols]
        adj_weekly = sum(eight_week_sales) / len(eight_week_sales)
        if adj_weekly < 0.01:
            continue
        woc = (row['stock']+row['bo']) / adj_weekly
        order_suggestion = (woc_target-woc)*adj_weekly
        result_df.set_value(sku, 'adj_weekly', adj_weekly) 
        result_df.set_value(sku, 'woc', woc) 
        result_df.set_value(sku, 'order_suggestion', order_suggestion) 
    result_df['weight'] = result_df['order_suggestion']*result_df['distri_cost']
    result_df = result_df.sort_values(by=['weight'])
    overstock_df = result_df.head(20)
    overstock_df = overstock_df.drop(columns=date_cols)
    lackstock_df = result_df.tail(20)[::-1]
    lackstock_df = lackstock_df.drop(columns=date_cols)
    return render_template(
        'distri_woc.html', 
        login = login_session, 
        account = account, 
        overstock_df = overstock_df,
        lackstock_df = lackstock_df,
        woc_target = woc_target, 
    )

# @app.route('/stock/download')
# @login_required(['ES'])
# def stockDownload():
#     result = session.query(
#             Stock.date.label('date'), 
#             Product.sku.label('sku'), 
#             Account.name.label('distributor'), 
#             func.sum(Stock.stock).label('stock'), 
#             func.sum(Stock.bo).label('bo'), 
#         ).filter(
#             Stock.account_id == Account.id, 
#             Stock.product_id == Product.id, 
#         ).group_by(
#             Stock.date, 
#             Product.sku, 
#             Account.name, 
#         ).order_by(
#             Stock.date.desc()
#         )
#     stock_df = pd.read_sql(
#             result.statement, 
#             result.session.bind, 
#         )
#     stock_df = stock_df.drop_duplicates(subset=['sku', 'distributor'])
#     writer = pd.ExcelWriter('/var/www/tplinkcrm/report/stock_report_{}.xlsx'.format(login_session['id']), engine='xlsxwriter')
#     stock_df.to_excel(writer)
#     writer.save()
#     return send_file('/var/www/tplinkcrm/report/stock_report_{}.xlsx'.format(login_session['id']), attachment_filename='stock_report.xlsx', as_attachment=True)

# @app.route('/woc/download')
# @login_required(['ES'])
# def wocDistriDownload():
#     args = request.args.to_dict()
#     args['redirecturl'] = request.path
#     account_id = request.args.get('account')
#     result = session.query(
#             Sellin.date.distinct().label('date')
#         ).filter(
#             Sellin.distri_id == account_id, 
#         ).order_by(
#             Sellin.date.desc()
#         ).first() 
#     sellin_recent_date = result.date
#     result = session.query(
#             Product.sku.label('sku'), 
#             Product.distri_cost.label('distri_cost'), 
#             Product.focused.label('focused'), 
#             Product.eol.label('eol'), 
#         )
#     result_df = pd.read_sql(
#             result.statement, 
#             result.session.bind, 
#             index_col = 'sku', 
#         )
#     for i in range(8):
#         delta = timedelta(days=7*i)
#         date_end = sellin_recent_date - delta
#         date_start = date_end - timedelta(days=7)
#         result = session.query(
#                 Product.sku.label('sku'), 
#                 func.sum(Sellin.qty).label('qty'), 
#             ).filter(
#                 Product.id == Sellin.product_id, 
#                 Sellin.distri_id == account_id, 
#                 Sellin.date > date_start, 
#                 Sellin.date <= date_end, 
#             ).group_by(
#                 'sku'
#             )
#         sellin_df = pd.read_sql(
#                 result.statement, 
#                 result.session.bind, 
#                 index_col = 'sku'
#             )
#         result_df[date_start] = sellin_df['qty']
#         # sellin_df = sellin_df.join(sellin_weekly_df)
#     result = session.query(
#             Stock.date.distinct().label('date')
#         ).filter(
#             Stock.account_id == account_id, 
#         ).order_by(
#             Stock.date.desc()
#         ).first()
#     stock_date = result.date
#     # Add stock column
#     result = session.query(
#             Product.sku.label('sku'), 
#             func.sum(Stock.stock).label('stock'), 
#         ).filter(
#             Product.id == Stock.product_id, 
#             Stock.account_id == account_id, 
#             Stock.date == stock_date, 
#         ).group_by(
#             Product.sku
#         )
#     stock_df = pd.read_sql(
#             result.statement, 
#             result.session.bind, 
#             index_col = 'sku', 
#         )
#     result_df['stock'] = stock_df['stock']
#     writer = pd.ExcelWriter('/var/www/tplinkcrm/report/woc_report_{}.xlsx'.format(login_session['id']), engine='xlsxwriter')
#     result_df.to_excel(writer)
#     writer.save()
#     return send_file('/var/www/tplinkcrm/report/woc_report_{}.xlsx'.format(login_session['id']), attachment_filename='woc_report.xlsx', as_attachment=True)

# @app.route('/stock/dashboard')
# @login_required(['ES'])
# def StockDashboard():
#     user = getUserById(login_session['id'])
#     accounts = session.query(
#             Account, 
#         ).filter(
#             Stock.account_id == Account.id, 
#         )
#     tp_accounts = accounts.filter(
#             Account.type == 'TP-Link'
#         ).all()
#     distri_accounts = accounts.filter(
#             Account.type == 'Distributor', 
#         ).all()
#     ka_accounts = accounts.filter(
#             Account.type != 'Distributor', 
#             Account.type != 'TP-Link', 
#             Account.ka == True
#         ).all()
#     role_dict = {
#         'Distributor': distri_accounts, 
#         'KA': ka_accounts, 
#         'TP': tp_accounts
#     }
#     stock_dict = {}
#     for account in accounts:
#         result = session.query(
#                 Stock.date.label('date'), 
#                 func.sum(Stock.total).label('total')
#             ).filter(
#                 Stock.account_id == account.id
#             ).group_by(
#                 Stock.date
#             ).order_by(
#                 Stock.date
#             ).all()
#         stock_dict[account.id] = result
#     return render_template(
#         'stock_dashboard.html', 
#         login = login_session, 
#         stock_dict = stock_dict, 
#         role_dict = role_dict, 
#     )

# @app.route('/sellout/dashboard-yoy')
# @login_required(['ES'])
# def SelloutDashboardYOY():
#     args = request.args.to_dict()
#     args['redirecturl'] = request.path
#     account_id = request.args.get('account')
#     account_name = session.query(
#             Sellout
#         ).filter(
#             Sellout.account_id == account_id
#         ).first().account.name
#     rows = session.query(
#             Productline.category.distinct().label('category'), 
#         ).all()
#     categories = [row.category for row in rows]
#     weights = []
#     rows = session.query(
#             func.extract('year', Sellout.date).distinct().label('year'), 
#         ).all()
#     years = [int(row.year) for row in rows]
#     result_dict = {} 
#     # Overview
#     result_dict['Overview'] = {} 
#     result = session.query(
#             Sellout.date.label('date'), 
#             func.sum(Sellout.total).label('total'), 
#             extract('year', Sellout.date).label('year')
#         ).join(
#             Product
#         ).join(
#             Productline
#         ).filter(
#             Sellout.product_id == Product.id, 
#             Product.pl_id == Productline.id, 
#         ).filter(
#             Sellout.account_id == account_id, 
#         ).order_by(
#             Sellout.date
#         ).group_by(
#             Sellout.date
#         )
#     result_df = pd.read_sql(result.statement, result.session.bind)
#     for year in years:
#         result_dict['Overview'][year] = result_df[result_df.year==year]
#     # By Category
#     for category in categories:
#         result_dict[category] = {} 
#         result = session.query(
#                 Sellout.date.label('date'), 
#                 func.sum(Sellout.total).label('total'), 
#                 extract('year', Sellout.date).label('year')
#             ).join(
#                 Product
#             ).join(
#                 Productline
#             ).filter(
#                 Sellout.product_id == Product.id, 
#                 Product.pl_id == Productline.id, 
#             ).filter(
#                 Productline.category == category, 
#                 Sellout.account_id == account_id, 
#             ).order_by(
#                 Sellout.date
#             ).group_by(
#                 Sellout.date
#             )
#         result_df = pd.read_sql(result.statement, result.session.bind)
#         for year in years:
#             result_dict[category][year] = result_df[result_df.year==year]
#         weight = result_df.total.sum()
#         weights.append(weight)
#     weights, categories = zip(*sorted(zip(weights, categories), reverse=True, key=lambda x: x[0]))
#     return render_template(
#         'sellout_dashboard_yoy.html', 
#         login = login_session, 
#         account_name = account_name, 
#         account_id = account_id, 
#         years = years, 
#         base_year = min(years), 
#         categories = categories, 
#         result_dict = result_dict, 
#     )

# @app.route('/category/<string:category>-yoy')
# @login_required(['ES'])
# def SelloutByCategoryYOY(category):
#     rows = session.query(
#             Productline.pl_wide.distinct().label('pl_wide'), 
#         ).filter(
#             Productline.category == category
#         ).all()
#     pl_wides = [row.pl_wide for row in rows]
#     weights = []
#     account_id = request.args.get('account')
#     account_name = session.query(
#             Sellout
#         ).filter(
#             Sellout.account_id == account_id
#         ).first().account.name
#     rows = session.query(
#             func.extract('year', Sellout.date).distinct().label('year'), 
#         ).all()
#     years = [int(row.year) for row in rows]
#     result_dict = {} 
#     for pl_wide in pl_wides:
#         result_dict[pl_wide] = {} 
#         result = session.query(
#                 Sellout.date.label('date'), 
#                 func.sum(Sellout.total).label('total'), 
#                 extract('year', Sellout.date).label('year')
#             ).join(
#                 Product
#             ).join(
#                 Productline
#             ).filter(
#                 Sellout.product_id == Product.id, 
#                 Product.pl_id == Productline.id, 
#             ).filter(
#                 Productline.category == category, 
#                 Productline.pl_wide == pl_wide, 
#                 Sellout.account_id == account_id, 
#             ).order_by(
#                 Sellout.date
#             ).group_by(
#                 Sellout.date
#             )
#         result_df = pd.read_sql(result.statement, result.session.bind)
#         weight = result_df.total.sum()
#         weights.append(weight)
#         for year in years:
#             result_dict[pl_wide][year] = result_df[result_df.year==year]
#     weights, pl_wides = zip(*sorted(zip(weights, pl_wides), reverse=True, key=lambda x: x[0]))
#     return render_template(
#         'sellout_by_category_yoy.html', 
#         login = login_session, 
#         account_name = account_name, 
#         account_id = account_id, 
#         years = years, 
#         base_year = min(years), 
#         category = category, 
#         pl_wides = pl_wides, 
#         result_dict = result_dict, 
#     )

# @app.route('/pl-wide/<pl_wide>-yoy')
# @login_required(['ES'])
# def SelloutByPlWideYOY(pl_wide):
#     category = request.args.get('category')
#     account_id = request.args.get('account')
#     account_name = session.query(
#             Sellout
#         ).filter(
#             Sellout.account_id == account_id
#         ).first().account.name
#     rows = session.query(
#             Productline.pl_narrow.distinct().label('pl_narrow'), 
#         ).filter(
#             Productline.category == category, 
#             Productline.pl_wide == pl_wide
#         )
#     rows = rows.all()
#     pl_narrows = [row.pl_narrow for row in rows]
#     weights = []
#     rows = session.query(
#             func.extract('year', Sellout.date).distinct().label('year'), 
#         ).all()
#     years = [int(row.year) for row in rows]
#     result_dict = {} 
#     for pl_narrow in pl_narrows:
#         result_dict[pl_narrow] = {} 
#         result = session.query(
#                 Sellout.date.label('date'), 
#                 func.sum(Sellout.total).label('total'), 
#                 extract('year', Sellout.date).label('year')
#             ).join(
#                 Product
#             ).join(
#                 Productline
#             ).filter(
#                 Sellout.product_id == Product.id, 
#                 Product.pl_id == Productline.id, 
#             ).filter(
#                 Productline.category == category, 
#                 Productline.pl_wide == pl_wide, 
#                 Productline.pl_narrow == pl_narrow, 
#                 Sellout.account_id == account_id, 
#             ).order_by(
#                 Sellout.date
#             ).group_by(
#                 Sellout.date
#             )
#         result_df = pd.read_sql(result.statement, result.session.bind)
#         weight = result_df.total.sum()
#         weights.append(weight)
#         for year in years:
#             result_dict[pl_narrow][year] = result_df[result_df.year==year]
#     weights, pl_narrows = zip(*sorted(zip(weights, pl_narrows), reverse=True, key=lambda x: x[0]))
#     return render_template(
#         'sellout_by_pl_wide_yoy.html', 
#         login = login_session, 
#         account_name = account_name, 
#         account_id = account_id, 
#         years = years, 
#         base_year = min(years), 
#         category = category, 
#         pl_wide = pl_wide, 
#         pl_narrows = pl_narrows, 
#         result_dict = result_dict, 
#     )

# @app.route('/pl-narrow/<pl_narrow>-yoy')
# @login_required(['ES'])
# def SelloutByPlNarrowYOY(pl_narrow):
#     category = request.args.get('category')
#     pl_wide = request.args.get('pl-wide')
#     account_id = request.args.get('account')
#     account_name = session.query(
#             Sellout
#         ).filter(
#             Sellout.account_id == account_id
#         ).first().account.name
#     rows = session.query(
#             Product.sku.distinct().label('sku'), 
#         ).join(
#             Productline
#         ).filter(
#             Product.pl_id == Productline.id, 
#         ).filter(
#             Productline.category == category, 
#             Productline.pl_wide == pl_wide, 
#             Productline.pl_narrow == pl_narrow, 
#         )
#     rows = rows.all()
#     skus = [row.sku for row in rows]
#     weights = []
#     rows = session.query(
#             func.extract('year', Sellout.date).distinct().label('year'), 
#         ).all()
#     years = [int(row.year) for row in rows]
#     result_dict = {} 
#     for sku in skus:
#         result_dict[sku] = {} 
#         result = session.query(
#                 Sellout.date.label('date'), 
#                 func.sum(Sellout.sellout).label('sellout'), 
#                 func.sum(Sellout.total).label('total'), 
#                 extract('year', Sellout.date).label('year')
#             ).join(
#                 Product
#             ).filter(
#                 Sellout.product_id == Product.id, 
#             ).filter(
#                 Product.sku == sku, 
#                 Sellout.account_id == account_id, 
#             ).order_by(
#                 Sellout.date
#             ).group_by(
#                 Sellout.date
#             )
#         result_df = pd.read_sql(result.statement, result.session.bind)
#         weight = result_df.total.sum()
#         weights.append(weight)
#         for year in years:
#             result_dict[sku][year] = result_df[result_df.year==year]
#     weights, skus = zip(*sorted(zip(weights, skus), reverse=True, key=lambda x: x[0]))
#     return render_template(
#         'sellout_by_pl_narrow_yoy.html', 
#         login = login_session, 
#         account_name = account_name, 
#         account_id = account_id, 
#         years = years, 
#         base_year = min(years), 
#         skus = skus, 
#         result_dict = result_dict, 
#     )

# @app.route('/pl-narrow/<pl_narrow>')
# @login_required(['ES'])
# def SelloutByPlNarrow(pl_narrow):
#     category = request.args.get('category')
#     pl_wide = request.args.get('pl-wide')
#     rows = session.query(
#             Sellout.account.distinct().label('account'), 
#         ).all()
#     accounts = [row.account for row in rows]
#     rows = session.query(
#             Product.sku.distinct().label('sku'), 
#         ).join(
#             Productline
#         ).filter(
#             Product.pl_id == Productline.id, 
#         ).filter(
#             Productline.category == category, 
#             Productline.pl_wide == pl_wide, 
#             Productline.pl_narrow == pl_narrow, 
#         )
#     rows = rows.all()
#     skus = [row.sku for row in rows]
#     weights = []
#     result_dict = {} 
#     for sku in skus:
#         result_dict[sku] = {}
#         weight = 0
#         for account in accounts: 
#             result = session.query(
#                     Sellout.date.label('date'), 
#                     func.sum(Sellout.sellout).label('sellout'), 
#                     func.sum(Sellout.total).label('total'), 
#                 ).join(
#                     Product
#                 ).filter(
#                     Sellout.product_id == Product.id, 
#                 ).filter(
#                     Product.sku == sku, 
#                     Sellout.account == account, 
#                 ).order_by(
#                     Sellout.date
#                 ).group_by(
#                     Sellout.date
#                 )
#             result_df = pd.read_sql(result.statement, result.session.bind)
#             result_dict[sku][account] = result_df
#             weight += result_df.total.sum()
#         weights.append(weight)
#     weights, skus = zip(*sorted(zip(weights, skus), reverse=True, key=lambda x: x[0]))
#     return render_template(
#         'sellout_by_pl_narrow.html', 
#         login = login_session, 
#         skus = skus, 
#         accounts = accounts, 
#         result_dict = result_dict, 
#     )

@app.route('/stock/history')
@login_required(['ES', 'DE'])
def StockHistory():
    user = getUserById(login_session['id'])
    sku = request.args.get('sku')
    accounts = session.query(
            Account, 
        ).filter(
            Stock.account_id == Account.id, 
            Account.country == user.country, 
        )
    tp_accounts = accounts.filter(
            Account.type == 'TP-LINK'
        ).all()
    distri_accounts = accounts.filter(
            Account.type == 'DISTRIBUTOR', 
        ).all()
    role_dict = {
        'Distributor': distri_accounts, 
        'TP': tp_accounts
    }
    stock_dict = {}
    for account in accounts:
        result = session.query(
                Stock.date.label('date'), 
                func.sum(Stock.stock).label('stock')
            ).filter(
                Stock.account_id == account.id, 
                Stock.product_id == Product.id, 
                Product.sku == sku, 
            ).group_by(
                Stock.date
            ).order_by(
                Stock.date
            ).all()
        stock_dict[account.id] = result
    return render_template(
        'stock_history.html', 
        login = login_session, 
        sku = sku, 
        stock_dict = stock_dict, 
        role_dict = role_dict, 
    )

@app.route('/price/query', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def priceQuery():
    products = session.query(
            Product
        ).all()
    return render_template(
        'price_query.html', 
        login = login_session, 
        products = products, 
    )

@app.route('/price/query/result', methods=['POST'])
@login_required(['ES', 'DE'])
def priceQueryResult():
    user = getUserById(login_session['id'])
    skus = request.form.get('skus').splitlines()
    skus = [sku.upper() for sku in skus if sku]
    price_df = pd.DataFrame(skus)
    price_df.columns = ['original_sku']
    result = session.query(
            SkuToProduct.sku.label('original_sku'), 
            Product.id.label('product_id'), 
        ).filter(
            Product.id == SkuToProduct.product_id, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    price_df = price_df.merge(result_df, on='original_sku', how='left')
    accounts, low_flag, result_df = get_price_info(user.country)
    price_df = price_df.merge(result_df, on='product_id', how='left')
    return render_template(
        'price_query_result.html', 
        login = login_session, 
        accounts = accounts,   
        price_df = price_df, 
    )

@app.route('/stock/query/result', methods=['POST'])
@login_required(['ES', 'DE'])
def StockQueryResult():
    user = getUserById(login_session['id'])
    accounts = session.query(
            Account
        ).filter(
            Account.id == Stock.account_id, 
            Account.country == user.country, 
        ).all()
    skus = request.form.get('skus').splitlines()
    skus = [sku.upper() for sku in skus if sku]
    stock_data = {}
    for sku in skus:
        stock_data[sku] = {}
        row = session.query(
                SkuToProduct
            ).filter(
                SkuToProduct.sku == sku
            ).first()
        if row:
            product_id = row.product.id
        else:
            product_id = -1
        stock_data[sku]['product_id'] = product_id
        for account in accounts: 
            row = session.query(
                    Stock.date.label('date'), 
                    func.sum(Stock.stock).label('stock')
                ).filter(
                    Stock.account_id == account.id, 
                    Stock.product_id == product_id, 
                ).group_by(
                    Stock.date
                ).order_by(
                    desc(Stock.date), 
                ).first()
            if row:
                qty = row.stock
                date = row.date
            else:
                date = -1
                qty = -1
            stock_data[sku][account.id] = {
                'qty': qty, 
                'date': date, 
            }
    return render_template(
        'stock_query_result.html', 
        login = login_session, 
        accounts = accounts,   
        skus = skus, 
        stock_data = stock_data, 
    )

@app.route('/stock/query', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def StockQuery():
    products = session.query(
            Product
        ).all()
    return render_template(
        'stock_query.html', 
        login = login_session, 
        products = products, 
    )

# @app.route('/focused/dashboard')
# @login_required(['ES'])
# def FocusedDashboard():
#     ACCOUNTS = ['AMZ', 'PCC']
#     focused_products = session.query(
#             Product
#         ).filter(
#             Product.focused == True
#         ).all()
#     sellout_dict = {}
#     skus = [e.sku for e in focused_products]
#     for sku in skus:
#         sellout_dict[sku] = {}
#         for account in ACCOUNTS: 
#             result = session.query(
#                     Sellout.date, 
#                     label('sellout', func.sum(Sellout.sellout))
#                 ).filter(
#                     SkuToProduct.sku == sku
#                 ).filter(
#                     SkuToProduct.product_id == Sellout.product_id
#                 ).filter(
#                     Sellout.account == account
#                 ).group_by(
#                     Sellout.date
#                 ).all()
#             result = [
#                 (int(time.mktime(date.timetuple())) * 1000, sellout) 
#                 for date, sellout in result
#             ]
#             sellout_dict[sku][account] = result
#     return render_template(
#         'focused_dashboard.html', 
#         login = login_session, 
#         show_form = False, 
#         accounts = ACCOUNTS, 
#         skus = skus, 
#         sellout_dict = sellout_dict, 
#     )

@app.route('/sku/query', methods=['POST'])
def skuQuery():
    sku_input = request.simplejson['skuInput']
    sku_input = '%' + sku_input + '%'
    results = session.query(
            Product.sku
        ).filter(
            Product.sku.ilike(sku_input)
        ).all()
    results = {
        'skus': results, 
    }
    return simplejson.dumps(results)

# @app.route('/sellout/query')
# @login_required(['ES'])
# def SelloutQuery():
#     product_id = request.args.get('product')
#     product = session.query(
#             Product
#         ).filter(
#             Product.id == product_id
#         ).first()
#     accounts = session.query(
#             Account
#         ).filter(
#             Account.id == Sellout.account_id
#         )
#     rows = session.query(
#             func.extract('year', Sellout.date).distinct().label('year'), 
#         ).all()
#     years = [int(row.year) for row in rows]
#     result_dict = {} 
#     for account in accounts:
#         result_dict[account.id] = {} 
#         result = session.query(
#                 Sellout.date.label('date'), 
#                 func.sum(Sellout.sellout).label('sellout'), 
#                 func.sum(Sellout.total).label('total'), 
#                 extract('year', Sellout.date).label('year')
#             ).join(
#                 Product
#             ).filter(
#                 Sellout.product_id == Product.id, 
#             ).filter(
#                 Product.id == product_id, 
#                 Sellout.account_id == account.id, 
#             ).order_by(
#                 Sellout.date
#             ).group_by(
#                 Sellout.date
#             )
#         result_df = pd.read_sql(result.statement, result.session.bind)
#         for year in years:
#             result_dict[account.id][year] = result_df[result_df.year==year]
#     return render_template(
#         'sellout_query_result.html', 
#         login = login_session, 
#         accounts = accounts, 
#         years = years, 
#         base_year = min(years), 
#         result_dict = result_dict, 
#         product = product, 
#     )

@app.route('/stock/upload-check', methods=['POST'])
@login_required(['admin', 'uploader'])
def stockUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    stock_file = request.files.get('stock-file')
    stock_df = pd.read_excel(stock_file)
    # Check whether header in submission is ok
    required_header = pd.Series(['Date', 'Distributor', 'SKU', 'Stock', 'BO'])
    header_err = required_header[~required_header.isin(stock_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    stock_df.rename(columns = {
            'Date': 'date', 
            'Distributor': 'distributor', 
            'SKU': 'sku', 
            'Stock': 'stock', 
            'BO': 'bo', 
        }, inplace = True)
    stock_df['distributor'] = stock_df['distributor'].apply(lambda x: x.strip().upper())
    stock_df['sku'] = stock_df['sku'].apply(lambda x: str(x).strip().upper())
    # Check whether stock in submission is ok
    stock_df['stock'] = stock_df['stock'].fillna(0)
    stock_col = pd.to_numeric(stock_df['stock'], errors='coerce')
    stock_err = stock_df['stock'][stock_col.isna()]
    if not stock_err.empty: 
        response['stock'] = stock_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['stock'] = 'pass'
    # Check whether bo in submission is ok
    bo_col = pd.to_numeric(stock_df['bo'], errors='coerce')
    stock_df['bo'] = stock_df['bo'].fillna(0)
    bo_err = stock_df['bo'][bo_col.isna()]
    if not bo_err.empty: 
        response['bo'] = bo_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['bo'] = 'pass'
    # Check whether date in submission is ok
    date_col = pd.to_datetime(stock_df['date'], format='%Y-%m-%d', errors='coerce')
    date_err = stock_df['date'][date_col.isna()]
    if not date_err.empty: 
        response['date'] = date_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['date'] = 'pass'
    # Check whether all SKUs are mapped
    skus = pd.unique(stock_df['sku'])
    skus = pd.Series(skus)
    unmapped_skus = get_unmapped_sku(skus)
    if not unmapped_skus.empty:
        response['sku'] = unmapped_skus.to_dict()
        return simplejson.dumps(response)
    else: 
        response['sku'] = 'pass'
    # Check whether all distributors are mapped
    account_names = pd.unique(stock_df['distributor'])
    account_names = pd.Series(account_names)
    unmapped_accounts = get_unmapped_account(account_names, user.country)
    if not unmapped_accounts.empty:
        response['distributor'] = unmapped_accounts.to_dict()
        return simplejson.dumps(response)
    else: 
        response['distributor'] = 'pass'
    # Check whether there are duplicated record
    account_names = pd.unique(stock_df['distributor'])
    account_names = pd.Series(account_names)
    date_col.sort_values(inplace=True) 
    date_start = date_col.iloc[0]
    date_end = date_col.iloc[-1]
    distri_ids = session.query(
            NameToAccount.account_id
        ).filter(
            Account.id == NameToAccount.account_id, 
            NameToAccount.name.in_(account_names), 
            Account.country == user.country, 
        )
    result = session.query(
            Stock.account_id.distinct().label('distri_id'), 
        ).filter(
            Stock.date >= date_start, 
            Stock.date <= date_end, 
            Stock.country == user.country, 
            Stock.account_id.in_(distri_ids), 
        )
    duplication_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            Account.name.label('distri_name'), 
            Account.id.label('distri_id'), 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    duplication_df = duplication_df.merge(result_df)
    duplication_df = duplication_df['distri_name']
    if not duplication_df.empty:
        response['duplication'] = duplication_df.to_dict()
        return simplejson.dumps(response)
    else: 
        response['duplication'] = 'pass'
    return simplejson.dumps(response)

@app.route('/stock/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadStock():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        stock_file = request.files.get('stock-file')
        stock_df = pd.read_excel(stock_file)
        raw_row_count = stock_df.shape[0]
        stock_df.rename(columns = {
                'Date': 'date', 
                'Distributor': 'distributor', 
                'SKU': 'sku', 
                'Stock': 'stock', 
                'BO': 'bo', 
            }, inplace = True)
        stock_df['date'] = pd.to_datetime(stock_df['date'], format='%Y-%m-%d')
        stock_df['distributor'] = stock_df['distributor'].apply(lambda x: x.strip().upper())
        stock_df['sku'] = stock_df['sku'].apply(lambda x: str(x).strip().upper())
        stock_df['stock'] = pd.to_numeric(stock_df['stock'])
        stock_col = pd.to_numeric(stock_df['stock'], errors='coerce')
        stock_df['bo'] = pd.to_numeric(stock_df['bo'])
        stock_df['bo'] = stock_df['bo'].fillna(0)
        result = session.query(
                NameToAccount.name.label('name'), 
                NameToAccount.account_id.label('account_id')
            ).filter(
                NameToAccount.country == user.country
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        distributor_map_df = result_df.rename(columns = {
                'name': 'distributor', 
                'account_id': 'distributor_id', 
            })
        stock_df = stock_df.merge(distributor_map_df, on='distributor', how='left')
        stock_df.drop(columns=['distributor'], inplace=True)
        result = session.query(
                SkuToProduct.sku.label('sku'), 
                SkuToProduct.product_id.label('product_id')
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        stock_df = stock_df.merge(result_df, on='sku', how='left')
        stock_df.drop(columns=['sku'], inplace=True)
        result = session.query(
                Product.id.label('product_id'), 
                Product.distri_cost.label('distri_cost')
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        stock_df = stock_df.merge(result_df, on='product_id', how='left')
        assert stock_df.shape[0] == raw_row_count
        for index, row in stock_df.iterrows():
            newStock = Stock(
                   date = row['date'], 
                   stock = row['stock'], 
                   bo = row['bo'], 
                   country = user.country, 
                   product_id = row['product_id'], 
                   account_id = row['distributor_id'], 
                   distri_cost = row['distri_cost'], 
                )
            session.add(newStock)
        session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'stock_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/account/upload-check', methods=['POST'])
@login_required(['admin'])
def accountUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    account_file = request.files.get('account-file')
    account_df = pd.read_excel(account_file, dtype=str)
    # Check whether header in submission is ok
    required_header = pd.Series(['Tax', 'Name', 'Street', 'Postcode', 'City'])
    header_err = required_header[~required_header.isin(account_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    account_df.rename(columns = {
            'Tax': 'tax', 
            'Name': 'name', 
            'Street': 'street', 
            'Postcode': 'postcode', 
            'City': 'city', 
        }, inplace = True)
    account_df['tax'] = account_df['tax'].apply(lambda x: x.strip().upper())
    account_df['name'] = account_df['name'].apply(lambda x: x.strip().upper())
    account_df['name'] = account_df['name'].apply(lambda x: re.sub('\s+', ' ', x))
    account_df['street'] = account_df['street'].apply(lambda x: x.strip().upper())
    account_df['postcode'] = account_df['postcode'].apply(lambda x: x.strip().upper())
    account_df['city'] = account_df['city'].apply(lambda x: x.strip().upper())
    # Check whether all customers are mapped
    account_names = pd.unique(account_df['name'])
    account_names = pd.Series(account_names)
    unmapped_accounts = get_unmapped_account(account_names, user.country)
    if not unmapped_accounts.empty:
        response['customer'] = unmapped_accounts.to_dict()
        return simplejson.dumps(response)
    else: 
        response['customer'] = 'pass'
    return simplejson.dumps(response)

@app.route('/account/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadCustomer():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        # Request comes from unmapped-customer
        unmapped_customers = request.form.get('unmapped-customer')
        if unmapped_customers:
            unmapped_customers = pd.read_json(unmapped_customers, typ='series')
            for unmapped_customer in unmapped_customers:
                count1 = session.query(
                    Account
                ).filter(
                    Account.name == unmapped_customer, 
                    Account.country == user.country, 
                ).count()
                count2 = session.query(
                    NameToAccount
                ).filter(
                    NameToAccount.name == unmapped_customer, 
                    NameToAccount.country == user.country,
                ).count()
                if count1 == 0 and count2 == 0:
                    newAccount = Account(
                        name = unmapped_customer, 
                        country = user.country, 
                        type = 'UNCATEGORIZED', 
                    )
                    session.add(newAccount)
                    newNameToAccount = NameToAccount(
                        name = unmapped_customer, 
                        country = user.country, 
                        account = newAccount, 
                    )
                    session.add(newNameToAccount)
                    session.commit()
            flash('Accounts added, you can upload data now')
            return "pass"
        # Request comes from file upload
        account_file = request.files.get('account-file')
        account_df = pd.read_excel(account_file, dtype=str)
        raw_row_count = account_df.shape[0]
        account_df.rename(columns = {
                'Tax': 'tax', 
                'Name': 'name', 
                'Street': 'street', 
                'Postcode': 'postcode', 
                'City': 'city', 
            }, inplace = True)
        account_df['tax'] = account_df['tax'].apply(lambda x: x.strip().upper())
        account_df['name'] = account_df['name'].apply(lambda x: x.strip().upper())
        account_df['name'] = account_df['name'].apply(lambda x: re.sub('\s+', ' ', x))
        account_df['street'] = account_df['street'].apply(lambda x: x.strip().upper())
        account_df['postcode'] = account_df['postcode'].apply(lambda x: x.strip().upper())
        account_df['city'] = account_df['city'].apply(lambda x: x.strip().upper())
        result = session.query(
                NameToAccount.name.label('name'), 
                NameToAccount.account_id.label('account_id')
            ).filter(
                NameToAccount.country == user.country
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        account_df = account_df.merge(result_df, on='name', how='left')
        assert account_df.shape[0] == raw_row_count
        for index, row in account_df.iterrows():
            account = session.query(
                    Account
                ).filter(
                    Account.id == row['account_id'], 
                ).first()
            if not account.street and row['street']:
                account.street = row['street']
            if not account.postcode and row['postcode']:
                account.postcode = row['postcode']
            if not account.city and row['city']:
                account.city = row['city']
            if not account.tax and row['tax']:
                account.tax = row['tax']
            session.add(account)
        session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'account_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/amazon-sellout/upload-check', methods=['POST'])
@login_required(['admin', 'uploader'])
def amazonSelloutUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    amazon_sellout_file = request.files.get('amazon-sellout-file')
    amazon_sellout_df = pd.read_excel(amazon_sellout_file)
    # Check whether header in submission is ok
    header = amazon_sellout_df.columns
    if header[0] != 'ASIN':
        response['header'] = {0: 'First column name must be ASIN'}
        return simplejson.dumps(response)
    date_header_raw = header[1:]
    date_header = pd.to_datetime(date_header_raw, format='%Y-%m-%d', errors='coerce')
    header_err = date_header_raw[date_header.isna()]
    if not header_err.empty: 
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    amazon_sellout_df.rename(columns = {
            'ASIN': 'asin', 
        }, inplace = True)
    # Check whether all ASINs are mapped
    asins = pd.unique(amazon_sellout_df['asin'])
    asins = pd.Series(asins)
    unmapped_asins = get_unmapped_sku(asins.apply(lambda x: 'AMAZON-{}'.format(str(x).strip().upper())))
    if not unmapped_asins.empty:
        response['asin'] = unmapped_asins.to_dict()
        return simplejson.dumps(response)
    else: 
        response['asin'] = 'pass'
    return simplejson.dumps(response)

@app.route('/amazon-sellout/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadAmazonSellout():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        amazon = session.query(
                Account
            ).filter(
                Account.name == 'AMAZON {}'.format(user.country)
            ).first()
        if not amazon:
            flash('Please set up AMAZON {} as an account first'.format(user.country))
            return redirect('/')
        amazon_sellout_file = request.files.get('amazon-sellout-file')
        amazon_sellout_df = pd.read_excel(amazon_sellout_file)
        amazon_sellout_df.rename(columns = {
                'ASIN': 'asin', 
            }, inplace = True)
        # Remove duplicated ASIN
        amazon_sellout_df.drop_duplicates(subset='asin', inplace=True)
        amazon_sellout_df.rename(columns=lambda x: pd.to_datetime(x, format='%Y-%m-%d', errors='ignore'), inplace=True)
        date_header = amazon_sellout_df.columns[1:]
        # date_header = pd.to_datetime(date_header, format='%Y-%m-%d', errors='coerce')
        amazon_sellout_df['sku'] = amazon_sellout_df['asin'].apply(lambda x: 'AMAZON-{}'.format(str(x).strip().upper()))
        result = session.query(
                SkuToProduct.sku.label('sku'), 
                SkuToProduct.product_id.label('product_id')
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        amazon_sellout_df = amazon_sellout_df.merge(result_df, on='sku', how='left')
        for sellout_date in date_header:
            result = session.query(
                    Sellout
                ).filter(
                    Sellout.account_id == amazon.id, 
                    Sellout.date == sellout_date, 
                ).first()
            if result:
                continue
            for index, row in amazon_sellout_df.iterrows():
                newSellout = Sellout(
                       date = sellout_date, 
                       qty = row[sellout_date], 
                       account_id = amazon.id, 
                       product_id = row.product_id, 
                    )
                session.add(newSellout)
            session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'amazon_sellout_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/sellin/upload-check', methods=['POST'])
@login_required(['admin', 'uploader'])
def sellinUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    sellin_file = request.files.get('sellin-file')
    sellin_df = pd.read_excel(sellin_file)
    # Check whether header in submission is ok
    required_header = pd.Series(['Date', 'SKU', 'Distributor', 'Unit Price', 'Qty'])
    header_err = required_header[~required_header.isin(sellin_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    sellin_df.rename(columns = {
            'SKU': 'sku', 
            'Date': 'date', 
            'Customer': 'customer', 
            'Distributor': 'distributor', 
            'Unit Price': 'unit_price', 
            'Qty': 'qty', 
        }, inplace = True)
    sellin_df['distributor'] = sellin_df['distributor'].apply(lambda x: x.strip().upper())
    sellin_df['customer'] = sellin_df['customer'].apply(lambda x: x.strip().upper())
    sellin_df['customer'] = sellin_df['customer'].apply(lambda x: re.sub('\s+', ' ', x))
    sellin_df['sku'] = sellin_df['sku'].apply(lambda x: str(x).strip().upper())
    # Check whether unit price in submission is ok
    unit_price_col = pd.to_numeric(sellin_df['unit_price'], errors='coerce')
    unit_price_err = sellin_df['unit_price'][unit_price_col.isna() | (unit_price_col<0)]
    if not unit_price_err.empty: 
        response['unit_price'] = unit_price_err.to_dict()
        return simplejson.dumps(response, ignore_nan=True)
    else:
        response['unit_price'] = 'pass'
    # Check whether qty in submission is ok
    qty_col = pd.to_numeric(sellin_df['qty'], errors='coerce')
    qty_err = sellin_df['qty'][qty_col.isna() | (qty_col.abs()<0.001)]
    if not qty_err.empty: 
        response['qty'] = qty_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['qty'] = 'pass'
    # Check whether date in submission is ok
    date_col = pd.to_datetime(sellin_df['date'], format='%Y-%m-%d', errors='coerce')
    date_err = sellin_df['date'][date_col.isna()]
    if not date_err.empty: 
        response['date'] = date_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['date'] = 'pass'
    # Check whether all SKUs are mapped
    skus = pd.unique(sellin_df['sku'])
    skus = pd.Series(skus)
    unmapped_skus = get_unmapped_sku(skus)
    if not unmapped_skus.empty:
        response['sku'] = unmapped_skus.to_dict()
        return simplejson.dumps(response)
    else: 
        response['sku'] = 'pass'
    # Check whether all distributors are mapped
    account_names = pd.unique(sellin_df['distributor'])
    account_names = pd.Series(account_names)
    unmapped_accounts = get_unmapped_account(account_names, user.country)
    if not unmapped_accounts.empty:
        response['distributor'] = unmapped_accounts.to_dict()
        return simplejson.dumps(response)
    else: 
        response['distributor'] = 'pass'
    # Check whether all customers are mapped
    account_names = pd.unique(sellin_df['customer'])
    account_names = pd.Series(account_names)
    unmapped_accounts = get_unmapped_account(account_names, user.country)
    if not unmapped_accounts.empty:
        response['customer'] = unmapped_accounts.to_dict()
        return simplejson.dumps(response)
    else: 
        response['customer'] = 'pass'
    # Check whether there are duplicated record
    account_names = pd.unique(sellin_df['distributor'])
    account_names = pd.Series(account_names)
    date_col.sort_values(inplace=True) 
    date_start = date_col.iloc[0]
    date_end = date_col.iloc[-1]
    distri_ids = session.query(
            NameToAccount.account_id
        ).filter(
            Account.id == NameToAccount.account_id, 
            NameToAccount.name.in_(account_names), 
            Account.country == user.country, 
        )
    result = session.query(
            Sellin.distri_id.distinct().label('distri_id'), 
        ).filter(
            Sellin.date >= date_start, 
            Sellin.date <= date_end, 
            Sellin.country == user.country, 
            Sellin.distri_id.in_(distri_ids), 
        )
    duplication_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            Account.name.label('distri_name'), 
            Account.id.label('distri_id'), 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    duplication_df = duplication_df.merge(result_df)
    duplication_df = duplication_df['distri_name']
    if not duplication_df.empty:
        response['duplication'] = duplication_df.to_dict()
        return simplejson.dumps(response)
    else: 
        response['duplication'] = 'pass'
    return simplejson.dumps(response)

@app.route('/asin/upload-check', methods=['POST'])
@login_required(['admin', 'uploader'])
def asinUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    asin_file = request.files.get('asin-file')
    asin_df = pd.read_excel(asin_file)
    # Check whether header in submission is ok
    required_header = pd.Series(['SKU', 'ASIN'])
    header_err = required_header[~required_header.isin(asin_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    asin_df.rename(columns = {
            'SKU': 'sku', 
            'ASIN': 'asin', 
        }, inplace = True)
    asin_df['sku'] = asin_df['sku'].apply(lambda x: x.strip().upper())
    asin_df['sku'] = asin_df['sku'].apply(lambda x: re.sub('\s+', ' ', x))
    asin_df['asin'] = asin_df['asin'].apply(lambda x: x.strip().upper())
    # Check whether all SKU are mapped
    skus = pd.unique(asin_df['sku'])
    skus = pd.Series(skus)
    unmapped_skus = get_unmapped_sku(skus)
    if not unmapped_skus.empty:
        response['sku'] = unmapped_skus.to_dict()
        return simplejson.dumps(response)
    else: 
        response['sku'] = 'pass'
    return simplejson.dumps(response)

@app.route('/asin/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadAsin():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        asin_file = request.files.get('asin-file')
        asin_df = pd.read_excel(asin_file)
        asin_df = asin_df.fillna('')
        raw_row_count = asin_df.shape[0]
        asin_df.rename(columns = {
                'SKU': 'sku', 
                'ASIN': 'asin', 
            }, inplace = True)
        asin_df['sku'] = asin_df['sku'].apply(lambda x: x.strip().upper())
        asin_df['sku'] = asin_df['sku'].apply(lambda x: re.sub('\s+', ' ', x))
        asin_df['asin'] = asin_df['asin'].apply(lambda x: str(x).strip().upper())
        result = session.query(
                SkuToProduct.sku.label('sku'), 
                SkuToProduct.product_id.label('product_id')
            )
        sku_map_df = pd.read_sql(result.statement, result.session.bind)
        asin_df = asin_df.merge(sku_map_df, on='sku', how='left')
        assert asin_df.shape[0] == raw_row_count
        for index, row in asin_df.iterrows():
            product = session.query(
                    Product
                ).filter(
                    Product.id == row.product_id, 
                ).first()
            if row.asin:
                product.asin = row.asin
            session.add(product)
        session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'asin_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/account-detail/upload-check', methods=['POST'])
@login_required(['admin', 'uploader'])
def accountDetailUploadCheck():
    user = getUserById(login_session['id'])
    response = {}
    account_detail_file = request.files.get('account-detail-file')
    account_detail_df = pd.read_excel(account_detail_file)
    # Check whether header in submission is ok
    required_header = pd.Series(['Customer', 'Tax', 'Street', 'Postcode', 'City'])
    header_err = required_header[~required_header.isin(account_detail_df.columns)]
    if not header_err.empty:
        response['header'] = header_err.to_dict()
        return simplejson.dumps(response)
    else:
        response['header'] = 'pass'
    account_detail_df.rename(columns = {
            'Customer': 'customer', 
            'Tax': 'tax', 
            'Street': 'street', 
            'Postcode': 'postcode', 
            'City': 'city', 
        }, inplace = True)
    account_detail_df['customer'] = account_detail_df['customer'].apply(lambda x: x.strip().upper())
    account_detail_df['customer'] = account_detail_df['customer'].apply(lambda x: re.sub('\s+', ' ', x))
    # Check whether all customers are mapped
    account_names = pd.unique(account_detail_df['customer'])
    account_names = pd.Series(account_names)
    unmapped_accounts = get_unmapped_account(account_names, user.country)
    if not unmapped_accounts.empty:
        response['customer'] = unmapped_accounts.to_dict()
        return simplejson.dumps(response)
    else: 
        response['customer'] = 'pass'
    return simplejson.dumps(response)

@app.route('/account-detail/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadAccountDetail():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        account_detail_file = request.files.get('account-detail-file')
        account_detail_df = pd.read_excel(account_detail_file)
        account_detail_df = account_detail_df.fillna('')
        raw_row_count = account_detail_df.shape[0]
        account_detail_df.rename(columns = {
                'Customer': 'customer', 
                'Tax': 'tax', 
                'Street': 'street', 
                'Postcode': 'postcode', 
                'City': 'city', 
            }, inplace = True)
        account_detail_df['customer'] = account_detail_df['customer'].apply(lambda x: x.strip().upper())
        account_detail_df['customer'] = account_detail_df['customer'].apply(lambda x: re.sub('\s+', ' ', x))
        account_detail_df['tax'] = account_detail_df['tax'].apply(lambda x: str(x).strip().upper())
        account_detail_df['street'] = account_detail_df['street'].apply(lambda x: str(x).strip().upper())
        account_detail_df['postcode'] = account_detail_df['postcode'].apply(lambda x: str(x).strip().upper())
        account_detail_df['city'] = account_detail_df['city'].apply(lambda x: str(x).strip().upper())
        result = session.query(
                NameToAccount.name.label('name'), 
                NameToAccount.account_id.label('account_id')
            ).filter(
                NameToAccount.country == user.country
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        customer_map_df = result_df.rename(columns = {
                'name': 'customer', 
                'account_id': 'customer_id', 
            })
        account_detail_df = account_detail_df.merge(customer_map_df, on='customer', how='left')
        assert account_detail_df.shape[0] == raw_row_count
        for index, row in account_detail_df.iterrows():
            account = session.query(
                    Account
                ).filter(
                    Account.id == row.customer_id, 
                ).first()
            if row.tax:
                account.tax = row.tax
            if row.street:
                account.street = row.street
            if row.postcode:
                account.postcode = row.postcode
            if row.city:
                account.city = row.city
            session.add(account)
        session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'account_detail_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/sellin/upload', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def uploadSellin():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        sellin_file = request.files.get('sellin-file')
        sellin_df = pd.read_excel(sellin_file)
        raw_row_count = sellin_df.shape[0]
        sellin_df.rename(columns = {
                'SKU': 'sku', 
                'Date': 'date', 
                'Customer': 'customer', 
                'Distributor': 'distributor', 
                'Unit Price': 'unit_price', 
                'Qty': 'qty', 
            }, inplace = True)
        sellin_df['distributor'] = sellin_df['distributor'].apply(lambda x: x.strip().upper())
        sellin_df['customer'] = sellin_df['customer'].apply(lambda x: x.strip().upper())
        sellin_df['customer'] = sellin_df['customer'].apply(lambda x: re.sub('\s+', ' ', x))
        sellin_df['sku'] = sellin_df['sku'].apply(lambda x: str(x).strip().upper())
        sellin_df['unit_price'] = pd.to_numeric(sellin_df['unit_price'])
        sellin_df['qty'] = pd.to_numeric(sellin_df['qty'])
        sellin_df['date'] = pd.to_datetime(sellin_df['date'], format='%Y-%m-%d')
        result = session.query(
                NameToAccount.name.label('name'), 
                NameToAccount.account_id.label('account_id')
            ).filter(
                NameToAccount.country == user.country
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        distributor_map_df = result_df.rename(columns = {
                'name': 'distributor', 
                'account_id': 'distributor_id', 
            })
        sellin_df = sellin_df.merge(distributor_map_df, on='distributor', how='left')
        sellin_df.drop(columns=['distributor'], inplace=True)
        customer_map_df = result_df.rename(columns = {
                'name': 'customer', 
                'account_id': 'customer_id', 
            })
        sellin_df = sellin_df.merge(customer_map_df, on='customer', how='left')
        result = session.query(
                SkuToProduct.sku.label('sku'), 
                SkuToProduct.product_id.label('product_id')
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        sellin_df = sellin_df.merge(result_df, on='sku', how='left')
        sellin_df.drop(columns=['sku'], inplace=True)
        sellin_df['unit_price'] = (sellin_df['unit_price']*100).astype(int)
        assert sellin_df.shape[0] == raw_row_count
        for index, row in sellin_df.iterrows():
            newSellin = Sellin(
                   date = row['date'], 
                   qty = row['qty'], 
                   unit_price = row['unit_price'], 
                   country = user.country, 
                   original_account = row['customer'], 
                   product_id = row['product_id'], 
                   distri_id = row['distributor_id'], 
                   account_id = row['customer_id'], 
                )
            session.add(newSellin)
        session.commit()
        flash('Successfully uploaded')
        return redirect('/')
    else:
        return render_template(
            'sellin_upload.html', 
            login = login_session, 
            user = user, 
        )

@app.route('/report-by-account/start')
@login_required(['ES', 'DE'])
def reportByAccountStart():
    user = getUserById(login_session['id'])
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, result.date.month, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    result = session.query(
            Account.type.distinct(), 
        ).filter(
            Account.country == user.country
        ).all()
    types = [e[0] for e in result]
    types.remove('TP-LINK')
    types.remove('DISTRIBUTOR')
    result = session.query(
            Productline.category.distinct(), 
        ).all()
    categories = [e[0] for e in result]
    return render_template(
        'report_by_account_start.html', 
        login = login_session, 
        report_range = report_range, 
        types = types, 
        categories = categories, 
    )

@app.route('/report/dashboard/start')
@login_required(['ES', 'DE'])
def reportDashboardStart():
    user = getUserById(login_session['id'])
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    result = session.query(
            Account.type.distinct(), 
        ).filter(
            Account.country == user.country
        ).all()
    types = [e[0] for e in result]
    types.remove('TP-LINK')
    types.remove('DISTRIBUTOR')
    return render_template(
        'report_dashboard_start.html', 
        login = login_session, 
        report_range = report_range, 
        types = types, 
    )

@app.route('/customerfinder/start')
@login_required(['DE'])
def customerFinderStart():
    user = getUserById(login_session['id'])
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    result = session.query(
            Account.type.distinct(), 
        ).filter(
            Account.country == user.country
        ).all()
    types = [e[0] for e in result]
    types.remove('TP-LINK')
    types.remove('DISTRIBUTOR')
    return render_template(
        'customer_finder_start.html', 
        login = login_session, 
        report_range = report_range, 
        types = types, 
    )

@app.route('/customerfinder/result')
@login_required(['DE'])
def customerFinderResult():
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_start = parsing_date(date_start)
    date_end = request.args.get('end')
    date_end = parsing_date(date_end)
    report_range = (date_start, date_end)
    this_year = date_end.year
    account_types = request.args.getlist('type')
    postcode = request.args.get('postcode')
    account_ids = session.query(
            Account.id
        ).filter(
            Account.type.in_(account_types), 
            Account.country == user.country, 
            Account.postcode.ilike(postcode+'%'), 
        )
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            Account.id.label('account_id'), 
            Account.id.in_(account_ids), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.country == user.country,
            Sellin.account_id == Account.id, 
            extract('year', Sellin.date) >= this_year-1, 
            extract('month', Sellin.date) >= date_start.month, 
            extract('month', Sellin.date) <= date_end.month, 
        )
    result = result.group_by(
            Account.id, 'year'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['year'] = result_df['year'].astype(int)
    sellin_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=['year'], 
            index=['account_id'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    sellin_df['weight'] = sellin_df[this_year] + sellin_df[this_year-1]
    sellin_df.sort_values(by=['weight'], ascending=False, inplace=True)
    sellin_df.drop(columns=['weight'], inplace=True)
    result = session.query(
            Account.id.label('account_id'), 
            Account.name.label('account_name'), 
            Account.type.label('type'), 
            Account.street.label('street'), 
            Account.postcode.label('postcode'), 
            Account.city.label('city'), 
        ).filter(
            Account.id.in_(account_ids), 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    sellin_df = sellin_df.merge(result_df, on='account_id')
    sellin_df = sellin_df.head(50)
    return render_template(
            'customer_finder_result.html', 
            login = login_session, 
            report_range = report_range, 
            sellin_df = sellin_df, 
        )

@app.route('/report/product/start')
@login_required(['ES', 'DE'])
def reportByProductStart():
    user = getUserById(login_session['id'])
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    result = session.query(
            Account.type.distinct(), 
        ).filter(
            Account.country == user.country
        ).all()
    types = [e[0] for e in result]
    types.remove('TP-LINK')
    types.remove('DISTRIBUTOR')
    result = session.query(
            Product.category.label('category'), 
            Product.sub_category.label('sub_category'), 
            Product.sku.label('sku'), 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    categories = result_df['category'].drop_duplicates()
    sub_categories = result_df['sub_category'].drop_duplicates()
    skus = result_df['sku'].drop_duplicates()
    return render_template(
        'report_by_product_start.html', 
        login = login_session, 
        report_range = report_range, 
        types = types, 
        categories = categories, 
        sub_categories = sub_categories, 
        skus = skus, 
    )

@app.route('/report/product')
@login_required(['ES', 'DE'])
def reportByProduct():
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_start = parsing_date(date_start)
    date_end = request.args.get('end')
    date_end = parsing_date(date_end)
    this_year = date_end.year
    report_range = (date_start, date_end)
    account_types = request.args.getlist('type')
    threshold = int(request.args.get('threshold'))
    sku = request.args.get('sku')
    category = request.args.get('category')
    sub_category = request.args.get('sub-category')
    account_ids = session.query(
            Account.id
        ).filter(
            Account.type.in_(account_types), 
            Account.country == user.country, 
        )
    result = session.query(
            Product.id
        )
    if sku:
        product_ids = result.filter(
                Product.sku == sku
            )
    if category:
        product_ids = result.filter(
                Product.category == category
            )
    if sub_category:
        product_ids = result.filter(
                Product.sub_category == sub_category
            )
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            extract('month', Sellin.date).label('month'), 
            Sellin.account_id.label('account_id'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.account_id.in_(account_ids), 
            Sellin.country == user.country, 
            Sellin.product_id.in_(product_ids), 
        ).group_by(
            'year', 'month', 'account_id'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['month'] = result_df['month'].astype(int)
    result_df['year'] = result_df['year'].astype(int)
    monthview_df = pd.pivot_table(
            result_df, 
            values=['revenue', 'qty'], 
            columns=['year'], 
            index=['month'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    customer_width_df = result_df[result_df['qty']>=1]
    customer_width_df = pd.pivot_table(
            customer_width_df, 
            values = 'account_id', 
            index = ['year'], 
            columns = ['month'], 
            aggfunc = 'count', 
            fill_value=0, 
        )
    customer_depth_df = result_df[result_df['qty']>=threshold]
    customer_depth_df = pd.pivot_table(
            customer_depth_df, 
            values = 'account_id', 
            index = ['year'], 
            columns = ['month'], 
            aggfunc = 'count', 
            fill_value=0, 
        )
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            Sellin.account_id.label('account_id'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.country == user.country,
            Sellin.account_id.in_(account_ids), 
            Sellin.product_id.in_(product_ids), 
            extract('year', Sellin.date) >= this_year-1, 
            extract('month', Sellin.date) >= date_start.month, 
            extract('month', Sellin.date) <= date_end.month, 
        )
    result = result.group_by(
            'account_id', 'year'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['year'] = result_df['year'].astype(int)
    progress_df = pd.pivot_table(
            result_df, 
            values='qty', 
            columns=['year'], 
            index=['account_id'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    progress_df['gap'] = progress_df[this_year] - progress_df[this_year-1]
    progress_df['weight'] = progress_df[this_year] + progress_df[this_year-1]
    progress_df.sort_values(by=['weight'], ascending=False, inplace=True)
    progress_df.drop(columns=['weight'], inplace=True)
    progress_df = progress_df.head(50)
    result = session.query(
            Account.id.label('account_id'), 
            Account.name.label('account_name')
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    progress_df = progress_df.merge(result_df, on='account_id')
    return render_template(
        'report_by_product.html', 
        login = login_session, 
        date_start = date_start, 
        date_end = date_end, 
        customer_width_df = customer_width_df, 
        customer_depth_df = customer_depth_df, 
        monthview_df = monthview_df, 
        progress_df = progress_df, 
        report_range = report_range, 
    )

@app.route('/report/dashboard')
@login_required(['ES', 'DE'])
def reportDashboard():
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_start = parsing_date(date_start)
    date_end = request.args.get('end')
    date_end = parsing_date(date_end)
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    account_types = request.args.getlist('type')
    threshold = int(request.args.get('threshold'))
    account_ids = session.query(
            Account.id
        ).filter(
            Account.type.in_(account_types), 
            Account.country == user.country, 
        )
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            extract('month', Sellin.date).label('month'), 
            Sellin.account_id.label('account_id'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.account_id.in_(account_ids), 
            Sellin.country == user.country, 
        ).group_by(
            'year', 'month', 'account_id'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['month'] = result_df['month'].astype(int)
    result_df['year'] = result_df['year'].astype(int)
    monthview_df = pd.pivot_table(
            result_df, 
            values=['revenue', 'qty'], 
            columns=['year'], 
            index=['month'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    customer_width_df = result_df[result_df['qty']>=1]
    customer_width_df = pd.pivot_table(
            customer_width_df, 
            values = 'account_id', 
            index = ['year'], 
            columns = ['month'], 
            aggfunc = 'count', 
            fill_value=0, 
        )
    customer_depth_df = result_df[result_df['qty']>=threshold]
    customer_depth_df = pd.pivot_table(
            customer_depth_df, 
            values = 'account_id', 
            index = ['year'], 
            columns = ['month'], 
            aggfunc = 'count', 
            fill_value=0, 
        )
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            Product.sku.label('sku'), 
            Sellin.distri_id.label('distri_id'), 
            Product.category.label('category'), 
            Product.sub_category.label('sub_category'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.account_id.in_(account_ids),
            Sellin.country == user.country,
            Sellin.product_id == Product.id,
            extract('month', Sellin.date) >= date_start.month, 
            extract('month', Sellin.date) <= date_end.month, 
        ).group_by(
            'year', 'category', 'sub_category', 'sku', 'distri_id', 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['year'] = result_df['year'].astype(int)
    overview_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=[], 
            index=['year'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    category_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=['year'], 
            index=['category'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    category_df['weight'] = category_df.sum(axis=1)
    category_df.sort_values(by=['weight'], ascending=False, inplace=True)
    category_df.drop(columns=['weight'], inplace=True)
    category_df = category_df.head(10)
    sub_category_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=['year'], 
            index=['sub_category'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    sub_category_df['weight'] = sub_category_df.sum(axis=1)
    sub_category_df.sort_values(by=['weight'], ascending=False, inplace=True)
    sub_category_df.drop(columns=['weight'], inplace=True)
    sub_category_df = sub_category_df.head(10)
    sku_df = pd.pivot_table(
            result_df, 
            values=['revenue', 'qty'], 
            columns=['year'], 
            index=['sku'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    sku_df['weight'] = sku_df.sum(axis=1)
    sku_df.sort_values(by=['weight'], ascending=False, inplace=True)
    sku_df.drop(columns=['weight'], inplace=True)
    sku_df = sku_df.head(10)
    return render_template(
        'report_dashboard.html', 
        login = login_session, 
        date_start = date_start, 
        date_end = date_end, 
        customer_width_df = customer_width_df, 
        customer_depth_df = customer_depth_df, 
        monthview_df = monthview_df, 
        overview_df = overview_df, 
        category_df = category_df, 
        sub_category_df = sub_category_df, 
        sku_df = sku_df, 
    )

@app.route('/report-by-account/result')
@login_required(['ES', 'DE'])
def reportResult():
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_end = request.args.get('end')
    report_range = (date_start, date_end)
    date_start = parsing_date(date_start)
    date_start_prev = date_start.replace(year=date_start.year-1)
    date_end = parsing_date(date_end)
    date_end_prev = date_end.replace(year=date_end.year-1)
    account_types = request.args.getlist('type')
    report_type = request.args.get('report-type')
    categories = request.args.getlist('category')
    if report_type in ['1', '2', '3']:
        result = session.query(
                Account.id.label('account_id'), 
                Account.name.label('account'), 
                func.sum(Sellin.unit_price * Sellin.qty).label('amount'), 
            ).filter(
                Sellin.account_id == Account.id, 
                Sellin.product_id == Product.id, 
                Sellin.country == user.country, 
                Product.pl_id == Productline.id, 
                Account.type.in_(account_types), 
                Productline.category.in_(categories), 
            ).filter(
                Sellin.date >= date_start, 
                Sellin.date <= date_end
            ).group_by(
                Account.id
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        sellin_df = result_df.set_index('account_id')
        result = session.query(
                Account.id.label('account_id'), 
                func.sum(Sellin.unit_price * Sellin.qty).label('benchmark'), 
            ).filter(
                Sellin.account_id == Account.id, 
                Sellin.product_id == Product.id, 
                Sellin.country == user.country, 
                Product.pl_id == Productline.id, 
                Account.type.in_(account_types), 
                Productline.category.in_(categories), 
            ).filter(
                Sellin.date >= date_start_prev, 
                Sellin.date <= date_end_prev
            ).group_by(
                Account.id
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df = result_df.set_index('account_id')
        sellin_df = sellin_df.join(result_df, how='outer')
        sellin_df['gap'] = sellin_df['amount'] - sellin_df['benchmark']
        if report_type == '1':
            sellin_df = sellin_df.sort_values(by='amount', ascending=False) 
        if report_type == '2':
            sellin_df = sellin_df.sort_values(by='gap', ascending=True) 
        elif report_type == '3':
            sellin_df = sellin_df.sort_values(by='gap', ascending=False) 
        sellin_df = sellin_df[:50]
        result = session.query(
                Account.id.label('account_id'),  
                Account.type.label('type'),  
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df = result_df.set_index('account_id')
        sellin_df = sellin_df.join(result_df)
        result = session.query(
                Account.id.label('account_id'),  
                User.name.label('manager'),  
            ).filter(
                Account.user_id == User.id
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df = result_df.set_index('account_id')
        sellin_df = sellin_df.join(result_df)
        return render_template(
            'top_fluc_accounts.html', 
            login = login_session, 
            sellin_df = sellin_df, 
            report_range = report_range, 
        )

# @app.route('/report-by-pl-wide/start')
# @login_required(['ES'])
# def reportByPlWideStart():
#     result = session.query(
#             Sellin
#         ).order_by(
#             Sellin.date.desc()
#         ).first()
#     report_day_start = date(result.date.year, result.date.month, 1)
#     last_day = monthrange(result.date.year, result.date.month)[1]
#     report_day_end = date(result.date.year, result.date.month, last_day)
#     report_range = (report_day_start, report_day_end)
#     result = session.query(
#             Account.type.distinct(), 
#         ).all()
#     types = [e[0] for e in result]
#     types.remove('TP-LINK')
#     types.remove('DISTRIBUTOR')
#     return render_template(
#         'report_by_pl_wide_start.html', 
#         login = login_session, 
#         report_range = report_range, 
#         types = types, 
#     )

def getProductById(product_id):
    return session.query(
            Product
        ).filter(
            Product.id == product_id
        ).one()

@app.route('/pricelink/edit', methods=['GET', 'POST'])
@login_required(['admin', 'uploader'])
def editPricelink():
    product_id = request.args.get('product')
    user = getUserById(login_session['id'])
    try:
        product_id = int(product_id)
    except:
        product_id = 0
    products = session.query(
            Product
        )
    rows = session.query(
            PriceLink.account.distinct().label('account')
        ).filter(
            PriceLink.country == user.country
        )
    accounts = [row.account for row in rows]
    pricelink_dict = {}
    if product_id != 0:
        result = session.query(
                PriceLink
            ).filter(
                PriceLink.product_id == product_id, 
                PriceLink.country == user.country, 
            )
        for row in result:
            pricelink_dict[row.account] = row.link
    if request.method == 'POST' and product_id != 0:
        price_link = None
        account = request.form.get('account')
        link = request.form.get('link')
        price = None
        price = query_online_price(user.country, account, link)
        product = getProductById(product_id)
        if price != None:
            price_link = session.query(
                    PriceLink
                ).filter(
                    PriceLink.account == account, 
                    PriceLink.country == user.country, 
                    PriceLink.product_id == product_id, 
                ).first()
            if not price_link:
                price_link = PriceLink(
                        account = account, 
                        country = user.country, 
                        product_id = product_id, 
                    )
            price_link.link = link
            session.add(price_link)
            session.commit()
            priceHistory = PriceHistory(
                timestamp = datetime.utcnow(), 
                price_link_id = price_link.id, 
                price = price, 
            )
            session.add(priceHistory)
            session.commit()
            flash('{} on {} with newest price {} updated'.format(product.sku, account, price/100))
            return redirect('pricelink/edit?product={}'.format(product_id))
        else:
            flash('Incorrect link, please double check')
    return render_template(
        'pricelink_edit.html', 
        login = login_session, 
        products = products, 
        accounts = accounts, 
        pricelink_dict = pricelink_dict,  
        product_id = product_id, 
    )

@app.route('/account/search', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def searchAccount():
    user = getUserById(login_session['id'])
    if request.method == 'POST':
        account_name = request.form.get('account-name')
        account_tax = request.form.get('account-tax')
        account_street = request.form.get('account-street')
        account_postcode = request.form.get('account-postcode')
        account_city = request.form.get('account-city')
        account_url = request.form.get('account-url')
        if not account_name:
            flash('Account name must not be empty')
            return render_template(
                'account_search.html', 
                login = login_session, 
                account_name = account_name, 
                account_df = account_df, 
                report_range = report_range, 
            )
        newAccount = Account(
                name = account_name.upper(), 
                country = user.country, 
                type = 'UNCATEGORIZED', 
            )
        if account_tax:
            newAccount.tax = account_tax.upper(), 
        if account_street:
            newAccount.street = account_street.upper(), 
        if account_postcode:
            newAccount.postcode = account_postcode.upper(), 
        if account_city:
            newAccount.city = account_city.upper(), 
        if account_url:
            newAccount.url = account_url, 
        session.add(newAccount)
        session.commit()
        newNameToAccount = NameToAccount(
                name = account_name.upper(), 
                country = user.country, 
                account_id = newAccount.id, 
            )
        session.add(newNameToAccount)
        session.commit()
        flash('Changes saved')
    account_name = request.args.get('account-name')
    result = session.query(
            Sellin
        ).order_by(
            Sellin.date.desc()
        ).first()
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    account_ids = session.query(
            NameToAccount.account_id.distinct()
        ).filter(
            NameToAccount.name.ilike('%'+account_name+'%'), 
            NameToAccount.country == user.country, 
        )
    result = session.query(
            Account.id.label('account_id'), 
            Account.name.label('account_name'), 
            Account.tax.label('account_tax'), 
            Account.street.label('account_street'), 
            Account.postcode.label('account_postcode'), 
            Account.city.label('account_city'), 
            Account.type.label('account_type'), 
        ).filter(
            Account.id.in_(account_ids), 
        )
    account_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            Account.id.label('account_id'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
        ).filter(
            Sellin.account_id == Account.id, 
            Sellin.date >= report_day_start, 
            Sellin.date <= report_day_end, 
        ).group_by(
            Account.id, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    account_df = account_df.merge(result_df, on='account_id', how='left')
    account_df.sort_values(by=['revenue'], ascending=False, inplace=True)
    return render_template(
        'account_search.html', 
        login = login_session, 
        account_name = account_name, 
        account_df = account_df, 
        report_range = report_range, 
    )

@app.route('/sellin-by-channel')
@login_required(['ES', 'DE'])
def sellinByChannel():
    user = getUserById(login_session['id'])
    channel = request.args.get('channel').upper()
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    recent_date = result.date
    this_year = result.date.year
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    progress_benchmark = (recent_date - report_day_start) / (report_day_end - report_day_start) - 1
    progress_benchmark = "{0:.0%}".format(progress_benchmark)
    if channel == 'AMAZON':
        result = session.query(
                Account
            ).filter(
                Account.country == user.country, 
                Account.name.ilike('%AMAZON%'), 
            ).one()
        amazon_id = result.id
        return redirect('/account/{}?start={}&end={}'.format(amazon_id, report_day_start, report_day_end))
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            Account.id.label('account_id'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.country == user.country,
            Sellin.account_id == Account.id, 
            extract('year', Sellin.date) >= this_year-1, 
            extract('month', Sellin.date) >= report_day_start.month, 
            extract('month', Sellin.date) <= report_day_end.month, 
        )
    if channel != 'TOTAL':
        result = result.filter(
                Account.type == channel, 
            )
    result = result.group_by(
            Account.id, 'year'
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['year'] = result_df['year'].astype(int)
    result_df['revenue'] = (result_df['revenue'] / 100).astype(int)
    progress_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=['year'], 
            index=['account_id'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    progress_df['gap'] = progress_df[this_year] - progress_df[this_year-1]
    progress_df['progress'] = progress_df[this_year] / progress_df[this_year-1] - 1
    progress_df['weight'] = progress_df[this_year] + progress_df[this_year-1]
    progress_df.sort_values(by=['weight'], ascending=False, inplace=True)
    progress_df.drop(columns=['weight'], inplace=True)
    progress_df = progress_df.head(50)
    progress_df['progress'] = progress_df['progress'].apply(lambda x: "{0:.0%}".format(x))
    result = session.query(
            Account.id.label('account_id'), 
            Account.name.label('account_name')
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    progress_df = progress_df.merge(result_df, on='account_id')
    return render_template(
            'sellin_by_channel.html', 
            login = login_session, 
            progress_df = progress_df, 
            progress_benchmark = progress_benchmark, 
            channel = channel, 
            report_range = report_range, 
        )

@app.route('/sellin/dashboard')
@login_required(['ES', 'DE'])
def sellinDashboard():
    user = getUserById(login_session['id'])
    result = session.query(
            Sellin
        ).filter(
            Sellin.country == user.country
        ).order_by(
            Sellin.date.desc()
        ).first()
    recent_date = result.date
    this_year = recent_date.year
    report_day_start = date(result.date.year, 1, 1)
    last_day = monthrange(result.date.year, result.date.month)[1]
    report_day_end = date(result.date.year, result.date.month, last_day)
    report_range = (report_day_start, report_day_end)
    progress_benchmark = (recent_date - report_day_start) / (report_day_end - report_day_start) - 1
    progress_benchmark = "{0:.0%}".format(progress_benchmark)
    result = session.query(
            Account.type.distinct(), 
        ).filter(
            Account.country == user.country
        ).all()
    account_types = [e[0] for e in result]
    account_types.remove('TP-LINK')
    account_types.remove('DISTRIBUTOR')
    account_types.append('ALL')
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            extract('month', Sellin.date).label('month'), 
            Account.type.label('account_type'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
        ).filter(
            Sellin.country == user.country,
            Sellin.account_id == Account.id, 
        ).group_by(
            'year', 'month', 'account_type', 
        )
    monthview_dict = {}
    for account_type in account_types:
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df['month'] = result_df['month'].astype(int)
        result_df['year'] = result_df['year'].astype(int)
        if account_type != 'ALL':
            result_df = result_df[result_df['account_type'] == account_type]
        monthview_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                index=['year'], 
                columns=['month'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        monthview_df['Total'] = monthview_df.sum(axis=1)
        monthview_dict[account_type] = monthview_df
    result = session.query(
            extract('year', Sellin.date).label('year'), 
            Account.type.label('account_type'), 
            func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            func.sum(Sellin.qty).label('qty'), 
        ).filter(
            Sellin.country == user.country,
            Sellin.account_id == Account.id, 
            extract('month', Sellin.date) >= report_day_start.month, 
            extract('month', Sellin.date) <= report_day_end.month, 
        ).group_by(
            'year', 'account_type',
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    result_df['year'] = result_df['year'].astype(int)
    result_df['revenue'] = (result_df['revenue'] / 100).astype(int)
    progress_df = pd.pivot_table(
            result_df, 
            values='revenue', 
            columns=['account_type'], 
            index=['year'], 
            aggfunc=np.sum, 
            fill_value=0
        )
    progress_df.rename(columns=lambda x: x.lower(), inplace=True)
    progress_df['total'] = progress_df.sum(axis=1)
    progress_df.drop(columns=['distributor'], inplace=True)
    progress_df = progress_df.transpose()
    progress_df['gap'] = progress_df[this_year] - progress_df[this_year-1]
    progress_df['progress'] = progress_df[this_year] / progress_df[this_year-1] - 1
    progress_df['progress'] = progress_df['progress'].apply(lambda x: "{0:.0%}".format(x))
    progress_df['weight'] = progress_df[this_year] + progress_df[this_year-1]
    progress_df.sort_values(by=['weight'], ascending=False, inplace=True)
    progress_df.drop(columns=['weight'], inplace=True)
    return render_template(
            'sellin_dashboard.html', 
            login = login_session, 
            monthview_dict = monthview_dict, 
            progress_df = progress_df, 
            progress_benchmark = progress_benchmark, 
            this_year = this_year, 
        )

@app.route('/sellin/detail', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def sellinDetail():
    user = getUserById(login_session['id'])
    account_id = request.args.get('account')
    result = session.query(
            Sellin.date.label('date'), 
            Sellin.product_id.label('product_id'), 
            Sellin.distri_id.label('distri_id'), 
            Sellin.account_id.label('account_id'), 
            Sellin.qty.label('qty'), 
            Sellin.unit_price.label('unit_price'), 
        ).filter(
            Sellin.account_id == account_id, 
            Sellin.country == user.country, 
        ).order_by(
            Sellin.date.desc()
        )
    sellin_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            Account.id.label('account_id'), 
            Account.name.label('account_name'), 
        ).filter(
            Account.country == user.country, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    sellin_df = sellin_df.merge(result_df, on='account_id', how='left')
    result = session.query(
            Account.id.label('distri_id'), 
            Account.name.label('distri_name'), 
        ).filter(
            Account.country == user.country, 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    sellin_df = sellin_df.merge(result_df, on='distri_id', how='left')
    result = session.query(
            Product.id.label('product_id'), 
            Product.sku.label('sku'), 
        )
    result_df = pd.read_sql(result.statement, result.session.bind)
    sellin_df = sellin_df.merge(result_df, on='product_id', how='left')
    return render_template(
            'sellin_detail.html', 
            login = login_session, 
            sellin_df = sellin_df, 
        )

@app.route('/account/<int:account_id>', methods=['GET', 'POST'])
@login_required(['ES', 'DE'])
def viewAccount(account_id):
    user = getUserById(login_session['id'])
    date_start = request.args.get('start')
    date_start = parsing_date(date_start)
    date_end = request.args.get('end')
    date_end = parsing_date(date_end)
    account = session.query(
            Account, 
        ).filter(
            Account.id == account_id, 
        ).first()
    account_partner_query = session.query(
            AccountPartner
        ).filter(
            AccountPartner.account_id == account_id
        )
    account_partner_db = [e.partner for e in account_partner_query]
    if request.method == 'POST':
        submission_type = request.form.get('submit')
        if submission_type == "new-note":
            note_type = request.form.get('type')
            note_creator = request.form.get('note-creator')
            note = request.form.get('note')
            if note_type:
                accountNote = AccountNote(
                        created = datetime.utcnow(), 
                        modified = datetime.utcnow(), 
                        type = note_type, 
                        note = note, 
                        account_id = account_id, 
                        user_id = note_creator, 
                    )
                session.add(accountNote)
                session.commit()
                flash('Changes saved')
            else:
                flash('Please select note type')
        elif submission_type == "edit-note":
            note_id = request.form.get('note-id')
            note = request.form.get('note')
            accountNote = session.query(
                AccountNote
            ).filter(
                AccountNote.id == note_id
            ).one()
            accountNote.note = note
            accountNote.modified = datetime.utcnow()
            session.add(accountNote)
            session.commit()
            flash('Changes saved')
        elif submission_type == "new-contact":
            contact_name = request.form.get('contact-name')
            contact_title = request.form.get('contact-title')
            contact_email = request.form.get('contact-email')
            contact_phone = request.form.get('contact-phone')
            contact_mobile = request.form.get('contact-mobile')
            contact_note = request.form.get('contact-note')
            contact_primary = request.form.get('contact-primary')
            contact_primary = bool(contact_primary)
            accountContact = AccountContact(
                    created = datetime.utcnow(), 
                    modified = datetime.utcnow(), 
                    name = contact_name, 
                    title = contact_title, 
                    email = contact_email, 
                    phone = contact_phone, 
                    mobile = contact_mobile, 
                    note = contact_note, 
                    primary = contact_primary, 
                    account_id = account_id, 
                    user_id = user.id, 
                )
            session.add(accountContact)
            session.commit()
            flash('Changes saved')
        elif submission_type == "edit-contact":
            contact_id = request.form.get('contact-id')
            contact_name = request.form.get('contact-name')
            contact_title = request.form.get('contact-title')
            contact_email = request.form.get('contact-email')
            contact_phone = request.form.get('contact-phone')
            contact_mobile = request.form.get('contact-mobile')
            contact_note = request.form.get('contact-note')
            contact_primary = request.form.get('contact-primary')
            contact_primary = bool(contact_primary)
            accountContact = session.query(
                AccountContact
            ).filter(
                AccountContact.id == contact_id
            ).one()
            accountContact.modified = datetime.utcnow()
            accountContact.name = contact_name
            accountContact.title = contact_title
            accountContact.email = contact_email
            accountContact.phone = contact_phone
            accountContact.mobile = contact_mobile
            accountContact.primary = contact_primary
            accountContact.note = contact_note
            session.add(accountContact)
            session.commit()
            flash('Changes saved')
        elif submission_type == "delete-contact":
            contact_id = request.form.get('contact-id')
            accountContact = session.query(
                AccountContact
            ).filter(
                AccountContact.id == contact_id
            ).delete()
            session.commit()
            flash('Changes saved')
        elif submission_type == "basic-info-edit":
            account_tax = request.form.get('account-tax')
            account_type = request.form.get('account-type')
            account_url = request.form.get('account-url')
            account_street = request.form.get('account-street')
            account_postcode = request.form.get('account-postcode')
            account_city = request.form.get('account-city')
            account_pam = request.form.get('account-pam')
            account_stage = request.form.get('account-stage')
            account_partners = request.form.getlist('account-partners')
            account_store = request.form.get('account-store')
            account_store = bool(account_store)
            if account_tax:
                account.tax = account_tax.upper()
            if account_type:
                account.type = account_type.upper()
            if account_url:
                account.url = account_url
            if account_street:
                account.street = account_street.upper()
            if account_postcode:
                account.postcode = account_postcode.upper()
            if account_city:
                account.city = account_city.upper()
            if account_pam:
                if account_pam.isdigit():
                    account.pam = int(account_pam) * 100
                else:
                    flash('PAM must be integer without . or ,')
            if account_stage:
                account.stage = account_stage.upper()
            account.store = account_store
            if account_partners:
                if set(account_partner_db) != set(account_partners):
                    account_partner_query.delete()
                    for account_partner in account_partners:
                        newAccountPartner = AccountPartner(
                                partner = account_partner, 
                                account_id = account_id
                            )
                        session.add(newAccountPartner)
                    session.commit()
                    account_partner_query = session.query(
                            AccountPartner
                        ).filter(
                            AccountPartner.account_id == account_id
                        )
                    account_partner_db = [e.partner for e in account_partner_query]
                    flash('Partner status updated')
                else: 
                    flash('Partner status unchanged')
            session.add(account)
            session.commit()
            flash('Changes saved')
    result = session.query(
            AccountNote.id, 
            AccountNote.created, 
            AccountNote.type, 
            AccountNote.note, 
            User.name.label('manager_name'), 
        ).filter(
            AccountNote.account_id == account_id, 
            AccountNote.user_id == User.id, 
        ).order_by(
            AccountNote.modified.desc()
        )
    account_note_df = pd.read_sql(result.statement, result.session.bind)
    result = session.query(
            AccountContact, 
        ).filter(
            AccountContact.account_id == account_id, 
        )
    account_contact_df = pd.read_sql(result.statement, result.session.bind)
    account_contact_df.rename(columns = {
            'name': 'contact_name', 
        }, inplace = True)
    managers = session.query(
            User
        ).filter(
            User.country == user.country
        )
    if account.type == 'DISTRIBUTOR':
        threshold = request.args.get('threshold')
        account_ids = session.query(
                Account.id
            ).filter(
                Account.country == user.country, 
            )
        result = session.query(
                extract('year', Sellin.date).label('year'), 
                extract('month', Sellin.date).label('month'), 
                Sellin.account_id.label('account_id'), 
                func.sum(Sellin.qty*Sellin.unit_price).label('revenue'), 
                func.sum(Sellin.qty).label('qty'), 
            ).filter(
                Sellin.distri_id == account_id, 
                Sellin.country == user.country, 
                Sellin.account_id.in_(account_ids), 
            ).group_by(
                'year', 'month', 'account_id'
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        customer_width_df = result_df[result_df['qty']>=1]
        customer_width_df = pd.pivot_table(
                customer_width_df, 
                values = 'account_id', 
                index = ['year'], 
                columns = ['month'], 
                aggfunc = 'count', 
                fill_value=0, 
            )
        if threshold:
            threshold = int(threshold)
        else:
            threshold = 5
        customer_depth_df = result_df[result_df['qty']>=threshold]
        customer_depth_df = pd.pivot_table(
                customer_depth_df, 
                values = 'account_id', 
                index = ['year'], 
                columns = ['month'], 
                aggfunc = 'count', 
                fill_value=0, 
            )
        return render_template(
                'view_distributor.html', 
                login = login_session, 
                account = account, 
                date_start = date_start, 
                date_end = date_end, 
                account_note_df = account_note_df, 
                account_contact_df = account_contact_df, 
                managers = managers, 
                user = user, 
                customer_width_df = customer_width_df, 
                customer_depth_df = customer_depth_df, 
            )
    # Not distributor
    else:
        result = session.query(
                func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            ).filter(
                Sellin.account_id == account_id,
                Sellin.country == user.country,
                Sellin.date >= date_start, 
                Sellin.date <= date_end, 
            ).first()
        ytd_revenue = 0
        if result.revenue:
            ytd_revenue = result.revenue
        result = session.query(
                func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            ).filter(
                Sellin.account_id == account_id,
                Sellin.country == user.country,
                Sellin.date >= date_end-timedelta(days=365), 
                Sellin.date <= date_end, 
            ).first()
        past_365_days_revenue = 0
        if result.revenue:
            past_365_days_revenue = result.revenue
        result = session.query(
                extract('year', Sellin.date).label('year'), 
                extract('month', Sellin.date).label('month'), 
                func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
            ).filter(
                Sellin.account_id == account_id,
                Sellin.country == user.country,
            ).group_by(
                'year', 'month'
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df['month'] = result_df['month'].astype(int)
        result_df['year'] = result_df['year'].astype(int)
        monthview_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                index=['year'], 
                columns=['month'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        monthview_df['Total'] = monthview_df.sum(axis=1)
        result = session.query(
                extract('year', Sellin.date).label('year'), 
                Product.sku.label('sku'), 
                Sellin.distri_id.label('distri_id'), 
                Product.category.label('category'), 
                Product.sub_category.label('sub_category'), 
                func.sum(Sellin.unit_price * Sellin.qty).label('revenue'), 
                func.sum(Sellin.qty).label('qty'), 
            ).filter(
                Sellin.account_id == account_id,
                Sellin.country == user.country,
                Sellin.product_id == Product.id,
                extract('month', Sellin.date) >= date_start.month, 
                extract('month', Sellin.date) <= date_end.month, 
            ).group_by(
                'year', 'category', 'sub_category', 'sku', 'distri_id', 
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        result_df['year'] = result_df['year'].astype(int)
        overview_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                columns=[], 
                index=['year'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        category_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                columns=['year'], 
                index=['category'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        category_df['weight'] = category_df.sum(axis=1)
        category_df.sort_values(by=['weight'], ascending=False, inplace=True)
        category_df.drop(columns=['weight'], inplace=True)
        category_df = category_df.head(10)
        sub_category_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                columns=['year'], 
                index=['sub_category'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        sub_category_df['weight'] = sub_category_df.sum(axis=1)
        sub_category_df.sort_values(by=['weight'], ascending=False, inplace=True)
        sub_category_df.drop(columns=['weight'], inplace=True)
        sub_category_df = sub_category_df.head(10)
        sku_df = pd.pivot_table(
                result_df, 
                values=['revenue', 'qty'], 
                columns=['year'], 
                index=['sku'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        sku_df['weight'] = sku_df.sum(axis=1)
        sku_df.sort_values(by=['weight'], ascending=False, inplace=True)
        sku_df.drop(columns=['weight'], inplace=True)
        sku_df = sku_df.head(10)
        distri_df = pd.pivot_table(
                result_df, 
                values='revenue', 
                columns=['year'], 
                index=['distri_id'], 
                aggfunc=np.sum, 
                fill_value=0
            )
        distri_df['weight'] = distri_df.sum(axis=1)
        distri_df.sort_values(by=['weight'], ascending=False, inplace=True)
        distri_df.drop(columns=['weight'], inplace=True)
        if not distri_df.empty:
            result = session.query(
                    Account.id.label('distri_id'), 
                    Account.name.label('distributor'),
                ).filter(
                    Account.country == user.country, 
                )
            result_df = pd.read_sql(result.statement, result.session.bind)
            distri_df = distri_df.merge(result_df, on='distri_id', how='left')
            distri_df.set_index('distributor',inplace=True)
            distri_df.drop(columns=['distri_id'], inplace=True)
        return render_template(
                'view_account.html', 
                login = login_session, 
                account = account, 
                monthview_df = monthview_df, 
                overview_df = overview_df, 
                category_df = category_df, 
                sub_category_df = sub_category_df, 
                sku_df = sku_df, 
                distri_df = distri_df, 
                date_start = date_start, 
                date_end = date_end, 
                account_note_df = account_note_df, 
                account_contact_df = account_contact_df, 
                managers = managers, 
                user = user, 
                ytd_revenue = ytd_revenue, 
                past_365_days_revenue = past_365_days_revenue, 
                account_partner_all = ACCOUNT_PARTNER_ALL, 
                account_partner_db = account_partner_db, 
            )

@app.route('/manager/<int:manager_id>')
@login_required(['ES', 'DE'])
def viewManager(manager_id):
    user = getUserById(login_session['id'])
    manager = session.query(
            User, 
        ).filter(
            User.id == manager_id, 
            User.country == user.country, 
        ).first()
    accounts = session.query(
            Account
        ).filter(
            Account.user_id == manager_id
        ).all()
    sellin_dict = {}
    weights = []
    result = session.query(
            Sellin
        ).order_by(
            Sellin.date.desc()
        ).limit(1)
    recent_date = result.first().date
    report_day_start = date(recent_date.year, 1, 1)
    report_range = (report_day_start, recent_date)
    result = session.query(
            func.extract('year', Sellin.date).distinct().label('year'), 
        ).all()
    years = [int(row.year) for row in result]
    years = sorted(years)
    revenue_dict = {}
    revenue_sum_df = pd.DataFrame()
    for account in accounts:
        result = session.query(
                extract('year', Sellin.date).label('year'), 
                extract('month', Sellin.date).label('month'), 
                func.sum(Sellin.unit_price * Sellin.qty).label('total'), 
            ).filter(
                Sellin.account_id == account.id, 
                Sellin.country == user.country, 
            ).group_by(
                'year', 'month'
            )
        result_df = pd.read_sql(result.statement, result.session.bind)
        sellin_df = pd.pivot_table(
                result_df, 
                values='total', 
                index=['year'], 
                columns=['month'], 
                aggfunc=np.sum
            )
        sellin_dict[account.id] = sellin_df
        revenue_sum_df = revenue_sum_df.add(sellin_df, fill_value=0)
        mtd_flag = sellin_df.columns <= recent_date.month
        revenue_df = sellin_df.loc[:, mtd_flag].sum(axis=1)
        revenue_dict[account.id] = revenue_df
        weight = sellin_df.sum().sum()
        weights.append(weight)
    sellin_dict['sum'] = revenue_sum_df
    mtd_flag = revenue_sum_df.columns <= recent_date.month
    revenue_sum_df = revenue_sum_df.loc[:, mtd_flag].sum(axis=1)
    revenue_dict['sum'] = revenue_sum_df
    weights, accounts = zip(*sorted(zip(weights, accounts), reverse=True, key=lambda x: x[0]))
    return render_template(
        'view_manager.html', 
        login = login_session, 
        manager = manager, 
        accounts = accounts, 
        years = years, 
        sellin_dict = sellin_dict, 
        revenue_dict = revenue_dict, 
        report_range = report_range, 
    )

@app.route('/distri-cost/upload', methods=['GET', 'POST'])
@login_required(['admin'])
def UploadDistriCost():
    if request.method == 'POST':
        distri_cost_data = request.form.get('distri-cost')
        lines = distri_cost_data.splitlines()
        upload_error = False
        unmapped_skus = set()
        for line in lines[1:]:
            if not line:
                continue
            line = line.upper()
            splitted = line.split('\t')
            sku_raw, distri_cost = splitted
            if not sku_raw:
                continue
            try: 
                skuToProduct = session.query(SkuToProduct).filter_by(sku=sku_raw).first()
                if not skuToProduct:
                    unmapped_skus.add(sku_raw)
                    raise NameError('SKU %s not found'%sku_raw)
                else:
                    product = skuToProduct.product
                    distri_cost = int(distri_cost)
            except (NameError, ValueError) as e:
                upload_error = True
                print(str(e))
            else: 
                product.distri_cost = distri_cost
                session.add(product)
        if not upload_error:
            session.commit()
            flash('Successfully uploaded')
        else:
            session.rollback()
            for unmapped_sku in unmapped_skus: 
                newSkuToProduct = SkuToProduct(
                    sku = unmapped_sku, 
                )
                session.add(newSkuToProduct)
            session.commit()
            flash('Upload error')
        return redirect('/')
    else:
        return render_template(
            'distri_cost_upload.html', 
            login = login_session, 
        )

# @app.route('/msrp/upload', methods=['GET', 'POST'])
# @login_required(['admin'])
# def UploadMsrp():
#     if request.method == 'POST':
#         msrp_data = request.form.get('msrp')
#         lines = msrp_data.splitlines()
#         upload_error = False
#         unmapped_skus = set()
#         for line in lines[1:]:
#             if not line:
#                 continue
#             line = line.upper()
#             splitted = line.split('\t')
#             sku_raw, msrp = splitted
#             if not sku_raw:
#                 continue
#             try: 
#                 skuToProduct = session.query(SkuToProduct).filter_by(sku=sku_raw).first()
#                 if not skuToProduct:
#                     unmapped_skus.add(sku_raw)
#                     raise NameError('SKU %s not found'%sku_raw)
#                 else:
#                     product = skuToProduct.product
#                     msrp = int(msrp)
#             except (NameError, ValueError) as e:
#                 upload_error = True
#                 print(str(e))
#             else: 
#                 product.msrp = msrp
#                 session.add(product)
#         if not upload_error:
#             session.commit()
#             flash('Successfully uploaded')
#         else:
#             session.rollback()
#             for unmapped_sku in unmapped_skus: 
#                 newSkuToProduct = SkuToProduct(
#                     sku = unmapped_sku, 
#                 )
#                 session.add(newSkuToProduct)
#             session.commit()
#             flash('Upload error')
#         return redirect('/')
#     else:
#         return render_template(
#             'msrp_upload.html', 
#             login = login_session, 
#         )

@app.route('/active/upload', methods=['GET', 'POST'])
@login_required(['admin'])
def UploadActiveProduct():
    if request.method == 'POST':
        active_product_data = request.form.get('active-product-data')
        lines = active_product_data.splitlines()
        upload_error = False
        unmapped_skus = set()
        result = session.query(Product).all()
        for product in result:
            product.eol = True
            session.add(product)
        session.commit()
        for line in lines[1:]:
            if not line:
                continue
            sku_raw = line.strip().upper()
            if not sku_raw:
                continue
            try: 
                skuToProduct = session.query(SkuToProduct).filter_by(sku=sku_raw).first()
                if not skuToProduct:
                    unmapped_skus.add(sku_raw)
                    raise NameError('SKU %s not found'%sku_raw)
                else:
                    product = skuToProduct.product
            except (NameError, ValueError) as e:
                upload_error = True
                print(str(e))
            else: 
                product.eol = False
                session.add(product)
        if not upload_error:
            session.commit()
            flash('Successfully uploaded')
        else:
            session.rollback()
            for unmapped_sku in unmapped_skus: 
                newSkuToProduct = SkuToProduct(
                    sku = unmapped_sku, 
                )
                session.add(newSkuToProduct)
            session.commit()
            flash('Upload error')
        return redirect('/')
    else:
        return render_template(
            'active_product_upload.html', 
            login = login_session, 
        )

# @app.route('/focused/upload', methods=['GET', 'POST'])
# @login_required(['admin'])
# def UploadFocusedProduct():
#     if request.method == 'POST':
#         focused_product_data = request.form.get('focused-product-data')
#         lines = focused_product_data.splitlines()
#         upload_error = False
#         unmapped_skus = set()
#         result = session.query(Product).filter_by(focused=True).all()
#         for product in result:
#             product.focused = False
#             session.add(product)
#         session.commit()
#         for line in lines[1:]:
#             if not line:
#                 continue
#             sku_raw = line.strip().upper()
#             if not sku_raw:
#                 continue
#             try: 
#                 skuToProduct = session.query(SkuToProduct).filter_by(sku=sku_raw).first()
#                 if not skuToProduct:
#                     unmapped_skus.add(sku_raw)
#                     raise NameError('SKU %s not found'%sku_raw)
#                 else:
#                     product = skuToProduct.product
#             except (NameError, ValueError) as e:
#                 upload_error = True
#                 print(str(e))
#             else: 
#                 product.focused = True
#                 session.add(product)
#         if not upload_error:
#             session.commit()
#             flash('Successfully uploaded')
#         else:
#             session.rollback()
#             for unmapped_sku in unmapped_skus: 
#                 newSkuToProduct = SkuToProduct(
#                     sku = unmapped_sku, 
#                 )
#                 session.add(newSkuToProduct)
#             session.commit()
#             flash('Upload error')
#         return redirect('/')
#     else:
#         return render_template(
#             'focused_product_upload.html', 
#             login = login_session, 
#         )

# @app.route('/signup', methods=['POST'])
# def signUp():
#     # Valid signup data
#     signup_status = {
#         'formValid': True, 
#     }
#     email = request.form['email']
#     if not valid_email(email):
#         signup_status['emailError'] = 'Invalid Email'
#         signup_status['formValid'] = False
#     passwd = request.form['passwd']
#     passwd_re = request.form['passwd-re']
#     if not valid_password(passwd):
#         signup_status['passwdError'] = 'Invalid Password'
#         signup_status['formValid'] = False
#     if passwd != passwd_re:
#         signup_status['passwdReError'] = 'Unmatched Password'
#         signup_status['formValid'] = False
#     if signup_status['formValid'] == True:
#         # Check whether user already exists
#         user = session.query(User).filter_by(email=email).first()
#         if user:
#             signup_status['emailError'] = 'User already registered, please login'
#             signup_status['formValid'] = False
#         else: 
#             salt = make_salt()
#             pw_hash = make_pw_hash(email, passwd, salt)
#             newUser = User(
#                 email=email, 
#                 salt=salt, 
#                 password=pw_hash, 
#             )
#             session.add(newUser)
#             session.commit()
#             login_session['email'] = email
#             login_session['id'] = newUser.id
#             login_session['roles'] = ''
#             print(newUser.id)
#     return simplejson.dumps(signup_status)

@app.route('/forgot', methods=['GET', 'POST'])
def forgotPwd():
    form_valid = True
    error_msg = {
        'emailError': '', 
    }
    if request.method == 'GET':
        return render_template(
            'forgot_password.html', 
            error_msg = error_msg, 
        )
    else:
        email = request.form.get('email')
        user = session.query(
                User
            ).filter(
                User.email == email
            ).first()
        if not user:
            error_msg['emailError'] = 'Email does not exist, please register'
            return render_template(
                'forgot_password.html', 
                error_msg = error_msg, 
            )
        token = make_salt(length=30)
        newResetPwdToken = ResetPwdToken(
                uid = user.id, 
                token = token, 
                timestamp = datetime.utcnow()
            )
        session.add(newResetPwdToken)
        session.commit()
        recipient = [user.email]
        body = reset_password_request_email.format(uid=user.id, token=token)
        send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'Reset password', body)
        flash('A reset link has been sent to your email')
        return redirect('/')
        
@app.route('/reset', methods=['GET', 'POST'])
def resetPwd():
    uid = request.args.get('uid')
    token = request.args.get('token')
    if not uid or not token:
        flash('No reset token found')
        return redirect('/')
    result = session.query(
            ResetPwdToken
        ).filter(
            ResetPwdToken.uid == uid, 
            ResetPwdToken.token == token, 
        ).order_by(
            ResetPwdToken.timestamp.desc()
        ).first()
    if not result:
        flash('Invalid reset token')
        return redirect('/')
    delta = datetime.utcnow() - result.timestamp
    if delta.total_seconds() > 24*60*60:
        flash('Reset token expired')
        return redirect('/')
    error_msg = {
        'passwdError': '', 
        'rePasswdError': '', 
    }
    if request.method == 'GET':
        return render_template(
            'reset_password.html', 
            error_msg = error_msg, 
            uid = uid, 
            token = token, 
        )
    else:
        form_valid = True
        passwd = request.form.get('passwd')
        re_passwd = request.form.get('re-passwd')
        if not valid_password(passwd): 
            form_valid = False
            error_msg['passwdError'] = 'Password length should be 3-20'
        if passwd != re_passwd:
            form_valid = False
            error_msg['rePasswdError'] = 'Password does not match'
        if not form_valid:
            return render_template(
                'reset_password.html', 
                error_msg = error_msg, 
                uid = uid, 
                token = token, 
            )
        user = session.query(
                User
            ).filter(
                User.id == uid
            ).first()
        if not user:
            flash('User does not exist')
        user.password = make_pw_hash(user.email, passwd, user.salt)
        session.add(user)
        session.delete(result)
        session.commit()
        body = reset_password_notification_email
        recipient = [user.email]
        send_email(EMAIL_USERNAME, EMAIL_PASSWORD, recipient, 'Password reseted', body)
        flash('Password reseted')
        return redirect('/')

# @app.route('/test', methods=['GET', 'POST'])
# @login_required(['admin'])
# def test():
#     from_arg = request.args.get('from')
#     return "You come from " + from_arg


@app.route('/login', methods=['GET', 'POST'])
def login():
    redirect_url = request.args.get('redirecturl')
    error_msg = {
        'emailError': '', 
        'passwdError': '', 
        'captchaError': '', 
    }
    args = request.args.to_dict()
    args.pop('redirecturl', None)
    redirect_url = redirect_url + '?' + '&'.join(['{}={}'.format(k, v) for k, v in args.items()])
    if request.method == 'GET':
        return render_template(
            'login.html', 
            error_msg = error_msg, 
            recaptcha = False, 
        )
    else: 
        pw_valid = False
        captcha_valid = False
        email = request.form.get('email')
        passwd = request.form.get('passwd')
        user = session.query(User).filter(User.email==email).first()
        if not user:
            error_msg['emailError'] = 'User does not exists, please contact james.guo@tp-link.com to register'
            return render_template(
                'login.html', 
                error_msg = error_msg, 
                recaptcha = False, 
            )
        # Below are codes for POST only
        # Check whether captcha is valid
        if user.login_failure <= 3:
            captcha_valid = True
        else:
            captcha_response = request.form.get('g-recaptcha-response')
            payload = {'response':captcha_response, 'secret':CAPTCHA_SECRET}
            response = requests.post("https://www.google.com/recaptcha/api/siteverify", payload)
            response_text = simplejson.loads(response.text)
            if response_text['success']:
                captcha_valid = True
        # Check whether pw is valid
        pw_hash = make_pw_hash(email, passwd, user.salt)
        if pw_hash == user.password:
            pw_valid = True
        # Successful login
        if pw_valid and captcha_valid:
            login_session['email'] = email
            login_session['id'] = user.id
            result = session.query(
                    Role
                ).filter(
                    Role.user_id == user.id
                )
            roles = [e.role for e in result]
            roles.append(user.country)
            login_session['roles'] = roles
            user.login_failure = 0
            session.add(user)
            session.commit()
            flash('Login successful')
            return redirect(redirect_url)
        # When captcha is invalid, return without leaking pw info
        elif not captcha_valid:
            error_msg['captchaError'] = 'Please pass the robot test'
        # When captcha is valid, but pw invlid, send pw tips
        else:
            error_msg['passwdError'] = 'Password does not match'
            user.login_failure += 1
            session.add(user)
            session.commit()
        return render_template(
            'login.html', 
            error_msg = error_msg, 
            recaptcha = user.login_failure > 3, 
        )

# Disconnect based on provider
@app.route('/logout')
def disconnect():
    if 'roles' in login_session:
        del login_session['email']
        del login_session['id']
        del login_session['roles']
        flash("You have successfully been logged out.")
        return redirect('/')
    else:
        flash("You were not logged in")
        return redirect('/')

def is_accessible(self):
    if 'roles' not in login_session:
        return False
    if 'admin' not in login_session['roles']:
        flash('Your need to be verified before using this function, please contact james.guo@tp-link.com for details')
        return False
    return True

ModelView.is_accessible = is_accessible

class SkuToProductView(ModelView):
    column_filters = ['sku', 'product']

class NameToAccountView(ModelView):
    column_filters = ['name', 'account']
    form_ajax_refs = {
        'account': {
            'fields': ('name', ),
        }
    }

class AccountView(ModelView):
    column_filters = ['name', 'tax', 'type']

class StockView(ModelView):
    column_filters = ['date', 'product', 'account']

class SelloutView(ModelView):
    column_filters = ['date', 'product', 'account']

class SellinView(ModelView):
    column_filters = ['date', 'product', 'original_account', 'account.name', 'distri.name', 'country']
    form_ajax_refs = {
        'account': {
            'fields': ('name', ),
        }, 
        'distri': {
            'fields': ('name', ),
        }, 
    }

class ProductView(ModelView):
    column_filters = ['sku', 'ean', 'asin', 'productline']

class ProductlineView(ModelView):
    column_filters = ['category_hq', 'category', 'pl_wide', 'pl_narrow']

class PriceLinkView(ModelView):
    column_filters = ['product', 'account']

class PriceHistoryView(ModelView):
    column_filters = ['price_link', 'price']

class AccountNoteView(ModelView):
    column_filters = ['type', 'note', 'account']
    form_ajax_refs = {
        'account': {
            'fields': ('name', ),
        }
    }

class PackingListView(ModelView):
    column_filters = ['so', 'ready_date', 'shipped_date', 'invoice_date']

app.secret_key = SECRET_KEY
app.debug = True
admin = Admin(app, name='tplink')
admin.add_view(ModelView(User, session))
admin.add_view(SelloutView(Sellout, session))
admin.add_view(SellinView(Sellin, session))
admin.add_view(ProductView(Product, session))
admin.add_view(StockView(Stock, session))
admin.add_view(SkuToProductView(SkuToProduct, session))
admin.add_view(PriceLinkView(PriceLink, session))
admin.add_view(PriceHistoryView(PriceHistory, session))
admin.add_view(ModelView(Task, session))
admin.add_view(AccountView(Account, session))
admin.add_view(NameToAccountView(NameToAccount, session))
admin.add_view(ModelView(ResetPwdToken, session))
admin.add_view(ProductlineView(Productline, session))
admin.add_view(ModelView(Role, session))
admin.add_view(ModelView(EmailSubscription, session))
admin.add_view(PackingListView(PackingListDetail, session))
admin.add_view(AccountNoteView(AccountNote, session))
admin.add_view(ModelView(AccountContact, session))
admin.add_view(ModelView(AccountPartner, session))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80)
