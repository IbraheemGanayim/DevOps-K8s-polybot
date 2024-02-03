import flask
from flask import request
import os
from bot import ObjectDetectionBot
import boto3
from loguru import logger
import json

app = flask.Flask(__name__)

# TODO load TELEGRAM_TOKEN value from Secret Manager
def load_telegram_token():
    secret_name = "batman-telegram-token"
    region_name = "eu-west-3"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        # Retrieve the secret value
        response = client.get_secret_value(SecretId=secret_name)
        secret_data = response['SecretString']
        return secret_data
    except Exception as e:
        print(f"Error retrieving Telegram token from Secret Manager: {e}")
        return None

TeleToken = load_telegram_token()
if TeleToken is None:
    raise ValueError("Failed to load TELEGRAM_TOKEN from Secret Manager")

TELEGRAM_TOKEN = json.loads(TeleToken)['TELEGRAM_TOKEN']
TELEGRAM_APP_URL = os.environ['TELEGRAM_APP_URL']

# Create a DynamoDB client
dynamodb_client = boto3.client('dynamodb', region_name='eu-west-3')

@app.route('/', methods=['GET'])
def index():
    return 'Ok'

@app.route(f'/{TELEGRAM_TOKEN}/', methods=['POST'])
def webhook():
    req = request.get_json()
    bot.handle_message(req['message'])
    return 'Ok'

@app.route(f'/results/', methods=['GET'])
def results():
    prediction_id = request.args.get('predictionId')

    # use the prediction_id to retrieve results from DynamoDB and send to the end-user
    prediction_summary = bot.get_item_by_prediction_id(prediction_id)
    
    logger.info(f'prediction_summary in results: {prediction_summary}')
    
    chat_id = prediction_summary['chat_id']
    text_results = bot.handle_dynamo_message(prediction_summary)
    bot.send_text(chat_id, text_results)
    return 'Ok'


@app.route(f'/loadTest/', methods=['POST'])
def load_test():
    req = request.get_json()
    bot.handle_message(req['message'])
    return 'Ok'


if __name__ == "__main__":
    bot = ObjectDetectionBot(TELEGRAM_TOKEN, TELEGRAM_APP_URL)
    app.run(host='0.0.0.0', port=8443)
