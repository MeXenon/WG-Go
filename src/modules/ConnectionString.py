import configparser
import os

from sqlalchemy_utils import database_exists, create_database

from DashboardConfig import DashboardConfig

def ConnectionString(database) -> str or None:    
    parser = configparser.ConfigParser(strict=False)
    parser.read_file(open('wg-dashboard.ini', "r+"))
    sqlitePath = os.path.join("db")
    if not os.path.isdir(sqlitePath):
        os.mkdir(sqlitePath)
    if parser.get("Database", "type") == "postgresql":
        cn = f'postgresql+psycopg2://{parser.get("Database", "username")}:{parser.get("Database", "password")}@{parser.get("Database", "host")}/{database}'
    elif parser.get("Database", "type") == "mysql":
        cn = f'mysql+mysqldb://{parser.get("Database", "username")}:{parser.get("Database", "password")}@{parser.get("Database", "host")}/{database}'
    else:
        cn = f'sqlite:///{os.path.join(sqlitePath, f"{database}.db")}'
    if not database_exists(cn):
        create_database(cn)
    return cn