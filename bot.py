import telebot
from loguru import logger
import os
import time
from telebot.types import InputFile
import requests
import boto3
import json
from collections import Counter

CERTIFICATE_FILE_NAME='YOURPUBLIC.pem'

class Bot:

    def __init__(self, token, telegram_chat_url):
        # create a new instance of the TeleBot class.
        # all communication with Telegram servers are done using self.telegram_bot_client
        self.telegram_bot_client = telebot.TeleBot(token)

        # remove any existing webhooks configured in Telegram servers
        self.telegram_bot_client.remove_webhook()
        time.sleep(0.5)

        # set the webhook URL
        self.telegram_bot_client.set_webhook(url=f'{telegram_chat_url}/{token}/', timeout=60, certificate=open(CERTIFICATE_FILE_NAME, 'r'))

        logger.info(f'Telegram Bot information\n\n{self.telegram_bot_client.get_me()}')

    def send_text(self, chat_id, text):
        self.telegram_bot_client.send_message(chat_id, text)

    def send_text_with_quote(self, chat_id, text, quoted_msg_id):
        self.telegram_bot_client.send_message(chat_id, text, reply_to_message_id=quoted_msg_id)

    def send_animation(self, chat_id, gif):
        return self.telegram_bot_client.send_animation(chat_id=chat_id, animation=gif)

    def delete_message(self, chat_id, msg_id):
        self.telegram_bot_client.delete_message(chat_id=chat_id, message_id=msg_id)
        
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

    session = boto3.Session()

    def handle_message(self, msg):

        if self.is_current_msg_photo(msg):

            # Send loading.gif while predicting
            with open('loading.gif', 'rb') as gif:
               loading_msg = self.send_animation(chat_id=msg['chat']['id'], gif=gif)
            
            # TODO download the user photo & upload the photo to S3
            photo_path = self.download_user_photo(msg)
            s3_bucket = os.environ['BUCKET_NAME']
            s3_path = "photos/" + os.path.basename(photo_path)
            uploaded = self.upload_to_s3(photo_path, s3_bucket, s3_path)

            if not uploaded:
                self.delete_message(msg['chat']['id'], loading_msg.message_id)
                self.send_text(msg['chat']['id'], "Failed to upload image to S3.")
            # If ulpoaded sucessfully
            else:
                # TODO send message to the Telegram end-user                
                self.send_text(msg['chat']['id'], "Your image is being processed.\nPlease wait...")
                
                # TODO send a job to the SQS queue
                queue_url = os.environ['SQS_QUEUE_URL']
                message_body = {
                    'photo_path': s3_path,
                    'chat_id': msg['chat']['id']
                }
                try:
                    sqs_client = self.session.client('sqs', region_name='eu-east-1')
                    response = sqs_client.send_message(
                        QueueUrl=queue_url,
                        MessageBody=json.dumps(message_body)
                    )
                    logger.info(f"Job sent to SQS with message ID: {response['MessageId']}")
                except Exception as e:
                    logger.error(f"Error sending job to SQS: {e}")

                # Delete loading gif
                self.delete_message(msg['chat']['id'], loading_msg.message_id)
        elif "text" in msg:
            self.send_text(msg['chat']['id'], f'Your original message: {msg["text"]}\nTip: Send a photo!')
        else:
            self.send_text(msg['chat']['id'], 'Unsupported message type.\nTip: Send a photo!')

    def handle_dynamo_message(self, dynamo_message):
        class_names = [label['M']['class']['S'] for label in dynamo_message['labels']]
        formatted_string = f'Objects Detected:\n'
        class_counts = Counter(class_names)
        json_string = json.dumps(class_counts)
        counts_dict = json.loads(json_string)
        for key,value in counts_dict.items():
            formatted_string += f'{key}: {value}\n'
        return formatted_string

    def get_item_by_prediction_id(self, prediction_id):
        dynamodb_client = self.session.client('dynamodb', region_name='eu-east-1')
        dynamo_tbl = 'ibraheemg-dynamodb-table'
        try:
            response = dynamodb_client.get_item(
                TableName=dynamo_tbl,
                Key={'prediction_id': {'S': prediction_id}}
             )
            pred_summary = response.get('Item', None)
            if pred_summary:
                pred_summary = {k: list(v.values())[0] for k, v in pred_summary.items()}
                return pred_summary
            else:
                print(f"No item found with prediction_id: {prediction_id}")
                return None
        except Exception as e:
            print(f"Error fetching item from DynamoDB: {e}")
            return None

    def upload_to_s3(self, file_path, bucket_name, object_name=None):
        if object_name is None:
            object_name = os.path.basename(file_path)
        s3_client = self.session.client('s3')
        try:
            s3_client.upload_file(file_path, bucket_name, object_name)
        except Exception as e:
            logger.error(f'\nERR Ulpoading: {e}\n')
            return False
        return True