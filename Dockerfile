FROM python:3.11-slim

COPY requirements.txt /tmp
RUN pip3 install -r /tmp/requirements.txt
RUN rm -f /tmp/requirements.txt

WORKDIR /opt/arlo-cam-api
COPY . ./

EXPOSE 4000/tcp
EXPOSE 4100/tcp
EXPOSE 5000/tcp

CMD python3 server.py
