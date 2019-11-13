from crm_config import *

from sqlalchemy import Column, Integer, String, Date, Boolean, Text, DateTime
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine
  
Base = declarative_base()

class ResetPwdToken(Base):
    __tablename__ = 'reset_pwd_token'

    id = Column(Integer, primary_key=True)
    uid = Column(Integer)
    token = Column(String(255))
    timestamp = Column(DateTime)

class SkuToProduct(Base):
    __tablename__ = 'sku_to_product'

    id = Column(Integer, primary_key=True)
    sku = Column(String(255), nullable=False)

    product_id = Column(Integer, ForeignKey('product.id'))
    product = relationship('Product')

class NameToAccount(Base):
    __tablename__ = 'name_to_account'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    country = Column(String(255))

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')

class AccountNote(Base):
    __tablename__ = 'account_note'

    id = Column(Integer, primary_key=True)
    created = Column(DateTime)
    modified = Column(DateTime)
    type = Column(String(255))
    note = Column(Text)

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')
    
    user_id = Column(Integer, ForeignKey('user.id'))
    user = relationship('User')

class AccountContact(Base):
    __tablename__ = 'account_contact'

    id = Column(Integer, primary_key=True)
    created = Column(DateTime)
    modified = Column(DateTime)
    name = Column(String(255))
    title = Column(String(255))
    email = Column(String(255))
    phone = Column(String(255))
    mobile = Column(String(255))
    primary = Column(Boolean)
    note = Column(Text)

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')
    
    user_id = Column(Integer, ForeignKey('user.id'))
    user = relationship('User')
    
class AccountPartner(Base):
    __tablename__ = 'account_partner'

    id = Column(Integer, primary_key=True)
    partner = Column(String(255), nullable=False)

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')

class Account(Base):
    __tablename__ = 'account'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    country = Column(String(255))
    tax = Column(String(255))
    street = Column(String(255))
    postcode = Column(String(255))
    city = Column(String(255))
    type = Column(String(255))
    manager = Column(String(255))
    url = Column(Text)
    pam = Column(Integer)
    target = Column(Integer)
    store = Column(Boolean)
    stage = Column(String(255))

    user_id = Column(Integer, ForeignKey('user.id'))
    manager = relationship('User')

    def __repr__(self):
        return self.name
 
class Role(Base):
    __tablename__ = 'role'
 
    id = Column(Integer, primary_key=True)
    role = Column(String(255))

    user_id = Column(Integer, ForeignKey('user.id'))
    user = relationship('User')

class EmailSubscription(Base):
    __tablename__ = 'email_subscription'
 
    id = Column(Integer, primary_key=True)
    email_list = Column(String(255))

    user_id = Column(Integer, ForeignKey('user.id'))
    user = relationship('User')

class User(Base):
    __tablename__ = 'user'
 
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    name = Column(String(255))
    country = Column(String(255))
    salt = Column(String(255), nullable=False)
    password = Column(String(255), nullable=False)
    login_failure = Column(Integer)

    def __repr__(self):
        return self.email

class Product(Base):
    __tablename__ = 'product'
    id = Column(Integer, primary_key=True)

    sku = Column(String(255), nullable=False)
    # All price stored in cents
    distri_cost = Column(Integer, nullable=False)
    msrp = Column(Integer)
    ean = Column(String(255))
    asin = Column(String(255))
    bu = Column(String(255))
    category = Column(String(255))
    sub_category = Column(String(255))
    focused = Column(Boolean)
    eol = Column(Boolean)

    pl_id = Column(Integer, ForeignKey('productline.id'))
    productline = relationship('Productline')

    def __repr__(self):
        return self.sku

class PriceLink(Base):
    __tablename__ = 'price_link'
 
    id = Column(Integer, primary_key=True)
    account = Column(String(255), nullable=False)
    country = Column(String(255))
    link = Column(Text, nullable=False)

    product_id = Column(Integer, ForeignKey('product.id'))
    product = relationship('Product')

    def __repr__(self):
        return '%s, %s'%(self.account, self.product.sku)

class Task(Base):
    __tablename__ = 'task'
 
    id = Column(Integer, primary_key=True)
    subject = Column(Text, nullable=False)
    detail = Column(Text)
    created = Column(DateTime)
    deadline = Column(DateTime)
    status = Column(String(255), nullable=False)

    task_from_id = Column(Integer, ForeignKey('user.id'))
    task_from = relationship('User', foreign_keys=[task_from_id])

    task_to_id = Column(Integer, ForeignKey('user.id'))
    task_to = relationship('User', foreign_keys=[task_to_id])

class PriceHistory(Base):
    __tablename__ = 'price_history'
 
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime)
    price = Column(Integer)

    price_link_id = Column(Integer, ForeignKey('price_link.id'))
    price_link = relationship('PriceLink')

class Productline(Base):
    __tablename__ = 'productline'
    id = Column(Integer, primary_key=True)

    pl_narrow = Column(String(255))
    pl_wide = Column(String(255))
    category = Column(String(255))
    category_hq = Column(String(255))

    def __repr__(self):
        return '%s, %s, %s, %s'%(self.id, self.pl_narrow, self.pl_wide, self.category)
    

class Stock(Base):
    __tablename__ = 'stock'
    id = Column(Integer, primary_key=True)

    date = Column(Date, nullable=False)
    stock = Column(Integer)
    bo = Column(Integer)
    distri_cost = Column(Integer)
    country = Column(String(255))

    product_id = Column(Integer, ForeignKey('product.id'))
    product = relationship('Product')

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')
  
class Sellout(Base):
    __tablename__ = 'sellout'

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    qty = Column(Integer)

    product_id = Column(Integer, ForeignKey('product.id'))
    product = relationship('Product')

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account')

class Sellin(Base):
    __tablename__ = 'sellin'

    id = Column(Integer, primary_key=True)
    original_account = Column(String(255))
    date = Column(Date, nullable=False)
    qty = Column(Integer)
    unit_price = Column(Integer)
    country = Column(String(255))

    product_id = Column(Integer, ForeignKey('product.id'))
    product = relationship('Product')

    distri_id = Column(Integer, ForeignKey('account.id'))
    distri = relationship('Account', foreign_keys=[distri_id])

    account_id = Column(Integer, ForeignKey('account.id'))
    account = relationship('Account', foreign_keys=[account_id])

class PackingListDetail(Base):
    __tablename__ = 'packing_list_detail'
    id = Column(Integer, primary_key=True)

    customer = Column(String(255))
    pi = Column(String(255))
    so = Column(String(255), unique=True)
    pl_name = Column(String(255))
    pl_date = Column(Date)
    promised_date = Column(Date)
    ready_date = Column(Date)
    shipped_date = Column(Date)
    invoice_date = Column(Date)
    truck_id = Column(String(255))
    required_date = Column(Date)
  
engine = create_engine('postgresql://{}:{}@{}'.format(DB_USERNAME, DB_PASSOWRD, DB_PATH))
Base.metadata.create_all(engine)
