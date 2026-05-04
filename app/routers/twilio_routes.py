# from fastapi import APIRouter, HTTPException
# from pydantic import BaseModel
# from twilio.rest import Client
# from dotenv import load_dotenv
# import os
#
# # Load environment variables
# load_dotenv()
#
# router = APIRouter()
#
# # Twilio credentials
# TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
# TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
# TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
#
# # Initialize Twilio client
# client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
#
# # Request body schema
# class SMSRequest(BaseModel):
#     phone_number: str
#     message: str
#
# @router.post("/send-sms")
# def send_sms(request: SMSRequest):
#     """
#     Send an SMS using Twilio.
#     """
#     try:
#         message = client.messages.create(
#             body=request.message,
#             from_=TWILIO_PHONE_NUMBER,
#             to=request.phone_number
#         )
#         return {
#             "status": "success",
#             "sid": message.sid,
#             "to": request.phone_number
#         }
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
