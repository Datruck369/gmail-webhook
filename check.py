import imaplib

EMAIL = 'argoxpd@gmail.com'
APP_PASSWORD = 'yqkvdjmqciybwazi'

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(EMAIL, APP_PASSWORD)

status, labels = mail.list()
for label in labels:
    print(label.decode())

mail.logout()
