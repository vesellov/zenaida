from __future__ import unicode_literals

ENV = 'development'
# ENV = 'production'

# Directly raise unexpected exceptions
# RAISE_EXCEPTIONS = True

#--- SQlite3
# DATABASES_ENGINE = 'django.db.backends.sqlite3'
# DATABASES_NAME = 'db.sqlite'

#--- Oracle
# DATABASES_ENGINE = 'django.db.backends.oracle'
# DATABASES_NAME = 'xe'
# DATABASES_USER = 'system'
# DATABASES_PASSWORD = '<password>'
# DATABASES_HOST = 'localhost'
# DATABASES_PORT = '49161'
# DATABASES_OPTIONS = {}
# DATABASES_TEST = dict(NAME='unittest.db')
# DATABASES_CONN_MAX_AGE = 0

#--- Postgres
# DATABASES_ENGINE = 'django.db.backends.postgresql_psycopg2'
# DATABASES_NAME = 'zenaida_db_01'
# DATABASES_USER = 'zenaida_db_user'
# DATABASES_PASSWORD = '<password>'
# DATABASES_HOST = 'localhost'
# DATABASES_PORT = ''


#--- EMAIL
# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# EMAIL_HOST = 'smtp.gmail.com'
# EMAIL_HOST_USER = 'your_account@gmail.com'
# EMAIL_HOST_PASSWORD = 'password is secret'
# EMAIL_PORT = 587
# EMAIL_USE_TLS = True
# EMAIL_USE_SSL = False


#--- Zenaida configuration 
# DEFAULT_REGISTRAR_ID = 'zenaida_registrar'
# SUPPORTED_ZONES = ['com', 'net', 'org', 'ai', ]
