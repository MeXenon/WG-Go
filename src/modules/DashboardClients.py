import hashlib
import uuid

import bcrypt
import pyotp
import sqlalchemy as db

from .ConnectionString import ConnectionString
from .DashboardClientsPeerAssignment import DashboardClientsPeerAssignment
from .DashboardClientsTOTP import DashboardClientsTOTP
from .Utilities import ValidatePasswordStrength
from .DashboardLogger import DashboardLogger

from flask import session


class DashboardClients:
    def __init__(self, wireguardConfigurations):
        self.logger = DashboardLogger()
        self.engine = db.create_engine(ConnectionString("wgdashboard"))
        self.metadata = db.MetaData()
        
        self.dashboardClientsTable = db.Table(
            'DashboardClients', self.metadata,
            db.Column('ClientID', db.String(255), nullable=False, primary_key=True),
            db.Column('Email', db.String(255), nullable=False, index=True),
            db.Column('Password', db.String(500)),
            db.Column('TotpKey', db.String(500)),
            db.Column('TotpKeyVerified', db.Integer),
            db.Column('CreatedDate', 
                      (db.DATETIME if 'sqlite:///' in ConnectionString("wgdashboard") else db.TIMESTAMP),
                      server_default=db.func.now()),
            db.Column('DeletedDate', 
                      (db.DATETIME if 'sqlite:///' in ConnectionString("wgdashboard") else db.TIMESTAMP)),
            extend_existing=True,
        )

        self.dashboardClientsInfoTable = db.Table(
            'DashboardClientsInfo', self.metadata,
            db.Column('ClientID', db.String(255), nullable=False, primary_key=True),
            db.Column('Firstname', db.String(500)),
            db.Column('Lastname', db.String(500)),
            extend_existing=True,   
        )

        self.metadata.create_all(self.engine)
        self.Clients = []
        self.__getClients()
        self.DashboardClientsTOTP = DashboardClientsTOTP()
        self.DashboardClientsPeerAssignment = DashboardClientsPeerAssignment(wireguardConfigurations)
        
    def __getClients(self):
        with self.engine.connect() as conn:
            self.Clients = conn.execute(
                db.select(
                    self.dashboardClientsTable.c.ClientID,
                    self.dashboardClientsTable.c.Email,
                    self.dashboardClientsTable.c.CreatedDate
                ).where(
                    self.dashboardClientsTable.c.DeletedDate is None)
                ).mappings().fetchall()
    
    def GetClientProfile(self, ClientID):
        with self.engine.connect() as conn:
            return dict(conn.execute(
                self.dashboardClientsInfoTable.select().where(
                    self.dashboardClientsInfoTable.c.ClientID == ClientID
                )
            ).mappings().fetchone())

    def SignIn(self, Email, Password) -> tuple[bool, str]:
        if not all([Email, Password]):
            return False, "Please fill in all fields"
        with self.engine.connect() as conn:
            existingClient = conn.execute(
                self.dashboardClientsTable.select().where(
                    self.dashboardClientsTable.c.Email == Email
                )
            ).mappings().fetchone()
            if existingClient:
                checkPwd = bcrypt.checkpw(Password.encode("utf-8"), existingClient.get("Password").encode("utf-8"))
                if checkPwd:
                    session['ClientID'] = existingClient.get("ClientID")
                    return True, self.DashboardClientsTOTP.GenerateToken(existingClient.get("ClientID"))
        return False, "Email or Password is incorrect"
    
    def SignIn_GetTotp(self, Token: str, UserProvidedTotp: str = None) -> tuple[bool, str] or tuple[bool, None, str]:
        status, data = self.DashboardClientsTOTP.GetTotp(Token)
        
        if not status:
            return False, "TOTP Token is invalid"    
        if UserProvidedTotp is None:
            if data.get('TotpKeyVerified') is None:
                return True, pyotp.totp.TOTP(data.get('TotpKey')).provisioning_uri(name=data.get('Email'),
                                                                                   issuer_name="WGDashboard Client")
        else:
            totpMatched = pyotp.totp.TOTP(data.get('TotpKey')).verify(UserProvidedTotp)
            if not totpMatched:
                return False, "TOTP is does not match"
            else:
                self.DashboardClientsTOTP.RevokeToken(Token)
        if data.get('TotpKeyVerified') is None:
            with self.engine.begin() as conn:
                conn.execute(
                    self.dashboardClientsTable.update().values({
                        'TotpKeyVerified': 1
                    }).where(
                        self.dashboardClientsTable.c.ClientID == data.get('ClientID')
                    )
                )
              
        return True, None
        
    def SignUp(self, Email, Password, ConfirmPassword) -> tuple[bool, str] or tuple[bool, None]:
        try:
            if not all([Email, Password, ConfirmPassword]):
                return False, "Please fill in all fields"
            if Password != ConfirmPassword:
                return False, "Passwords does not match"
    
            with self.engine.connect() as conn:
                existingClient = conn.execute(
                    self.dashboardClientsTable.select().where(
                        self.dashboardClientsTable.c.Email == Email
                    )
                ).mappings().fetchone()
                if existingClient:
                    return False, "Email already signed up"
    
            pwStrength, msg = ValidatePasswordStrength(Password)
            if not pwStrength:
                return pwStrength, msg
    
            with self.engine.begin() as conn:
                newClientUUID = str(uuid.uuid4())
                totpKey = pyotp.random_base32()
                encodePassword = Password.encode('utf-8')
                conn.execute(
                    self.dashboardClientsTable.insert().values({
                        "ClientID": newClientUUID,
                        "Email": Email,
                        "Password": bcrypt.hashpw(encodePassword, bcrypt.gensalt()).decode("utf-8"),
                        "TotpKey": totpKey
                    })
                )
                conn.execute(
                    self.dashboardClientsInfoTable.insert().values({
                        "ClientID": newClientUUID
                    })
                )
        except Exception as e:
            self.logger.log(Status="false", Message=f"Signed up failed, reason: {str(e)}")
            return False, "Signed up failed."
            
        return True, None
    
    def GetClientAssignedPeers(self, ClientID):
        return self.DashboardClientsPeerAssignment.GetAssignedPeers(ClientID)
    
    def UpdatePassword(self, CurrentPassword, NewPassword, ConfirmNewPassword):
        pass