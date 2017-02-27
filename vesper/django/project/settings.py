"""
Django settings for project project.

Generated by 'django-admin startproject' using Django 1.9.2.

For more information on this file, see
https://docs.djangoproject.com/en/1.9/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.9/ref/settings/
"""

import os


# Path of Vesper archive to be served.
VESPER_ARCHIVE_DIR_PATH = os.getcwd()

# Archive database file name.
VESPER_ARCHIVE_DATABASE_FILE_NAME = 'Archive Database.sqlite'

# TODO: It would be good to support clip and recording storage policies
# as Vesper extensions. Some possible examples of clip storage policies:
#
#     1. Don't store clips separate from recordings, but rather extract
#        them from their recordings when needed.
#
#     2. Store clips in batches in HDF5 files.
#
#     3. Store clips in files in a directory hierarchy organized by clip ID.
#
#     4. Store clips in files in a directory hierarchy organized by
#        clip station and time.
VESPER_CLIPS_DIR_FORMAT = (3, 3)

# Number of levels in the clips directory hierarchy. The maximum
# number of clips that a hierarchy with n levels can support is
# (10 ** (3 * n)) - 1, e.g. 1e9 - 1 for n = 3.
# VESPER_NUM_CLIPS_DIR_LEVELS = 3


# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.9/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'fi1dxvoed!-l9y-e7-2_m^l_if8qp2lixmggj&lk6(ad)4f+9g'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'vesper.django.app.apps.VesperConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE_CLASSES = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'vesper.django.project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'vesper.django.project.wsgi.application'


# Database
# https://docs.djangoproject.com/en/1.9/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(
            VESPER_ARCHIVE_DIR_PATH, VESPER_ARCHIVE_DATABASE_FILE_NAME),
    }
}


# Password validation
# https://docs.djangoproject.com/en/1.9/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/1.9/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'US/Eastern'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.9/howto/static-files/

STATIC_URL = '/static/'


# Directory to which the static files of all of the Django apps of this
# project are copied by "python manage.py collectstatic". Static files
# are served from this directory when Vesper is deployed on nginx/uWSGI,
# but not when it is deployed on the Django development server.
STATIC_ROOT = '/opt/vesper/static'
