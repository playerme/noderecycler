FROM google/cloud-sdk
ADD requirements.txt /requirements.txt
RUN pip install -r requirements.txt
ADD noderecycler.py /noderecycler.py
ENTRYPOINT ["python", "/noderecycler.py"]
