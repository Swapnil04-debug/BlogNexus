# smtp_test.py
from dotenv import load_dotenv
import os, smtplib

# Point at your .env file
load_dotenv()  

smtp = smtplib.SMTP('smtp.gmail.com', 587)
smtp.ehlo()
smtp.starttls()
smtp.login(os.getenv('MAIL_USERNAME'), os.getenv('MAIL_PASSWORD'))
print("Login successful!")
smtp.quit()
