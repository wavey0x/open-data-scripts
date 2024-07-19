from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Numeric, JSON, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
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

def insert_week_info(record):
    # Create a session
    session = Session()
    
    try:
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

def insert_user_info(record):
    session = Session()
    
    try:
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