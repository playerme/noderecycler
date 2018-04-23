FROM google/cloud-sdk:198.0.0-alpine
RUN apk add --no-cache py-pip
RUN pip install --upgrade pip
ADD requirements.txt /requirements.txt
RUN pip install -r requirements.txt
ADD noderecycler.py /noderecycler.py
ENTRYPOINT ["python", "/noderecycler.py"]
