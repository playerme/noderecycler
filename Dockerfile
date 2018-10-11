FROM python:3.7-alpine

# Upgrade pip and install pipenv
RUN pip install --upgrade pip
RUN pip install pipenv

# Install dependencies
ADD Pipfile .
ADD Pipfile.lock .
RUN pipenv install --system

ADD noderecycler.py /noderecycler.py
CMD ["python", "/noderecycler.py"]
