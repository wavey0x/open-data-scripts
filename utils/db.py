from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Boolean, Numeric, JSON, UniqueConstraint, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert

from dotenv import load_dotenv
import os

load_dotenv()
# Create an engine
engine = create_engine(os.getenv('DATABASE_URI'))  # Adjust the URL to your database

# Define the base class
Base = declarative_base()
# Bind the engine to the metadata of the Base class
Base.metadata.create_all(engine)

# Create a Session class
Session = sessionmaker(bind=engine)

class Stakes(Base):
    __tablename__ = 'stakes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ybs = Column(String)
    is_stake = Column(Boolean)
    account = Column(String)
    amount = Column(Numeric(30, 18))
    new_weight = Column(Numeric(30, 18))
    net_weight_change = Column(Numeric(30, 18))
    week = Column(Integer)
    txn_hash = Column(String)
    block = Column(Integer)
    timestamp = Column(Integer)
    date_str = Column(String)
    token = Column(String)

# Define the week_info table as a class
class WeekInfo(Base):
    __tablename__ = 'week_info'
    week_id = Column(Integer, primary_key=True)
    token = Column(String)
    weight = Column(Numeric(30, 18))
    total_supply = Column(Numeric(30, 18))
    boost = Column(Numeric(30, 18))
    ybs = Column(String, primary_key=True)
    stake_map = Column(JSON)
    start_ts = Column(Integer)
    end_ts = Column(Integer)
    start_block = Column(Integer)
    end_block = Column(Integer)
    start_time_str = Column(String)
    end_time_str = Column(String)
    __table_args__ = (
        UniqueConstraint('week_id', 'ybs', name='week_info_ybs_week_id_key'),
    )

class UserInfo(Base):
    __tablename__ = 'user_info'
    account = Column(String)
    week_id = Column(Integer, primary_key=True)
    token = Column(String)
    weight = Column(Numeric(30, 18))
    balance = Column(Numeric(30, 18))
    boost = Column(Numeric(30, 18))
    stake_map = Column(JSON)
    rewards_earned = Column(Numeric(30, 18))
    total_realized = Column(Numeric(30, 18))
    ybs = Column(String, primary_key=True)
    __table_args__ = (
        UniqueConstraint('account', 'ybs', 'week_id', name='user_info_account_ybs_week_id_key'),
    )

# Define metadata
metadata = MetaData()

# Define the table
stakes = Table('stakes', metadata,
    Column('account', String),
    Column('token', String),
    autoload_with=engine)



def query_unique_accounts(token):
    with engine.connect() as connection:
        query = select(stakes.c.account.distinct()).where(stakes.c.token == token)
        result = connection.execute(query)
        return [r[0] for r in result]

def test():
    token = '0xFCc5c47bE19d06BF83eB04298b026F81069ff65b'
    # Example usage
    accounts = query_unique_accounts(token)
    print(accounts)
    assert False

def insert_week_info(record, do_upsert):
    # Create a session
    session = Session()
    
    try:
        if do_upsert:
            stmt = insert(WeekInfo).values(**record).on_conflict_do_update(
                index_elements=['week_id', 'ybs'],
                set_={key: getattr(insert(WeekInfo).excluded, key) for key in record.keys()}
            )
            session.execute(stmt)
        else:
            week_info = WeekInfo(**record)
            session.add(week_info)
        
        # Commit the transaction
        session.commit()
    except Exception as e:
        # Rollback the transaction in case of error
        session.rollback()
        print(f"Error inserting record: {e}")
    finally:
        # Close the session
        session.close()
        

def insert_user_info(record, do_upsert=False):
    session = Session()
    
    try:
        if do_upsert:
            stmt = insert(UserInfo).values(**record).on_conflict_do_update(
                index_elements=['account', 'week_id', 'ybs'],  # Change this to your unique constraint column(s)
                set_={key: getattr(insert(UserInfo).excluded, key) for key in record.keys()}
            )
            session.execute(stmt)
        else:
            week_info = UserInfo(**record)
            session.add(week_info)
        
        # Commit the transaction
        session.commit()
        print("Record inserted successfully!")
    except Exception as e:
        # Rollback the transaction in case of error
        session.rollback()
        print(f"Error inserting record: {e}")
    finally:
        # Close the session
        session.close()

def get_latest_stake_recorded_for_token(token):
    session = Session()
    # Query to find the highest week_id for the given token
    highest_block = session.query(Stakes.block).\
        filter(Stakes.token == token).\
        order_by(Stakes.block.desc()).\
        first()

    # Check if we got a result
    if highest_block:
        return highest_block[0]  # highest_week_id is a tuple, so return the first element
    else:
        return None  # Return None if no rows were found

def get_highest_week_id_for_token(token):
    session = Session()
    # Query to find the highest week_id for the given token
    highest_week_id = session.query(WeekInfo.week_id).\
        filter(WeekInfo.token == token).\
        order_by(WeekInfo.week_id.desc()).\
        first()

    # Check if we got a result
    if highest_week_id:
        return highest_week_id[0]  # highest_week_id is a tuple, so return the first element
    else:
        return None  # Return None if no rows were found