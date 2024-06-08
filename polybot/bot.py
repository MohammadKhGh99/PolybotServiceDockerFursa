import boto3
import requests
import telebot
from flask import jsonify
from loguru import logger
import os
import time
from telebot.types import InputFile


class Bot:

    def __init__(self, token, telegram_chat_url):
        # create a new instance of the TeleBot class.
        # all communication with Telegram servers are done using self.telegram_bot_client
        self.telegram_bot_client = telebot.TeleBot(token)

        # remove any existing webhooks configured in Telegram servers
        self.telegram_bot_client.remove_webhook()
        time.sleep(0.5)

        # set the webhook URL
        self.telegram_bot_client.set_webhook(url=f'{telegram_chat_url}/'
                                                 f'{token}/', timeout=60,
                                            certificate=open("cert.pem", "r"))

        logger.info(f'Telegram Bot information\n\n{self.telegram_bot_client.get_me()}')

    def send_text(self, chat_id, text):
        self.telegram_bot_client.send_message(chat_id, text)

    def send_text_with_quote(self, chat_id, text, quoted_msg_id):
        self.telegram_bot_client.send_message(chat_id, text, reply_to_message_id=quoted_msg_id)

    def is_current_msg_photo(self, msg):
        return 'photo' in msg

    def download_user_photo(self, msg):
        """
        Downloads the photos that sent to the Bot to `photos` directory (should be existed)
        :return:
        """
        if not self.is_current_msg_photo(msg):
            raise RuntimeError(f'Message content of type \'photo\' expected')

        file_info = self.telegram_bot_client.get_file(msg['photo'][-1]['file_id'])
        data = self.telegram_bot_client.download_file(file_info.file_path)
        folder_name = file_info.file_path.split('/')[0]

        if not os.path.exists(folder_name):
            os.makedirs(folder_name)

        with open(file_info.file_path, 'wb') as photo:
            photo.write(data)

        return file_info.file_path

    def send_photo(self, chat_id, img_path):
        if not os.path.exists(img_path):
            raise RuntimeError("Image path doesn't exist")

        self.telegram_bot_client.send_photo(
            chat_id,
            InputFile(img_path)
        )

    def handle_message(self, msg):
        """Bot Main message handler"""
        logger.info(f'Incoming message: {msg}')
        self.send_text(msg['chat']['id'], f'Your original message: {msg["text"]}')


class ObjectDetectionBot(Bot):
    def handle_message(self, msg):
        logger.info(f'Incoming message: {msg}')
        usage_msg = 'Please send a photo to start object detection'
        if "text" in msg:
            if msg["text"] == '/start':
                self.send_text(msg['chat']['id'], 'Welcome to Object Detection Bot!')
                self.send_text(msg['chat']['id'], usage_msg)
                return
        if self.is_current_msg_photo(msg):
            photo_path = self.download_user_photo(msg)

            # upload the photo to S3
            logger.info(f'Uploading photo to S3: {photo_path}')
            session = boto3.Session()
            s3 = session.client('s3', 'us-east-1')
            bucket_name = os.getenv('BUCKET_NAME')
            s3.upload_file(photo_path, bucket_name, os.path.basename(photo_path))

            # send an HTTP request to the `yolo5` service for prediction
            # curl -X POST localhost:8081/predict?imgName=street.jpeg
            logger.info('Sending an HTTP request to the yolo5 service')
            params = {
                'imgName': os.path.basename(photo_path)
            }
            post_url = f'http://yolo:8081/predict'
            response = requests.post(post_url, params=params)
            # response = requests.post(f"localhost:8081/predict?imgName="
            #                          f"{os.path.basename(photo_path)}")

            # send the returned results to the Telegram end-user
            logger.info(f'Received response from yolo5 service: {response.text}')
            objects_rows = response.text.split('{\'class\':')
            msg_to_send = (f"We have found *{len(objects_rows) - 1}* objects "
                           f"in the image\n\n")
            msg_to_send += "Detected Objects:\n"
            objects = {}

            # first row has no object
            for row in objects_rows[1:]:
                object_name = row.split('\'')[1].strip()
                if object_name in objects:
                    objects[object_name] += 1
                else:
                    objects[object_name] = 1

            for object_name, count in objects.items():
                if count > 1:
                    msg_to_send += f'{object_name}s: {count}\n'
                else:
                    msg_to_send += f'{object_name}: {count}\n'

            msg_to_send += "\nObject Detection completed!"

            self.send_text(msg['chat']['id'], msg_to_send)
