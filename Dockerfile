FROM python:3.7-alpine

# Install packages dependencies to build cryptography module
ARG BUILD_DEPS="gcc musl-dev python3-dev libffi-dev openssl-dev"
RUN apk add --no-cache $BUILD_DEPS

# Upgrade pip and install pipenv
RUN pip install --upgrade pip
RUN pip install pipenv

# Install dependencies
ADD Pipfile .
ADD Pipfile.lock .
RUN pipenv install --system

# Remove build packages dependencies
RUN apk del $BUILD_DEPS

ADD noderecycler.py /noderecycler.py
CMD ["python", "/noderecycler.py"]
