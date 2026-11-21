import os
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from dotenv import load_dotenv
import aiosmtplib
from fastapi import BackgroundTasks

load_dotenv()

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.sender_email = os.getenv("SMTP_USER")
        self.sender_password = os.getenv("SMTP_PASSWORD")
        self.use_tls = False

    async def send_verification_email(self, to_email: str, verification_token: str, username: str) -> bool:
        verification_url = f"https://домен/verify-email?token={verification_token}"

        msg = MIMEMultipart('alternative')
        msg['Subject'] = "рирп - подтверждение email"
        msg['From'] = self.sender_email
        msg['To'] = to_email

        html = f"""
        <html>
          <body>
            <h2>Здравствуйте, {username}!</h2>
            <p>Спасибо за регистрацию.</p>
            <p>Для завершения регистрации подтвердите ваш email, нажав на кнопку ниже:</p>
            <p>
              <a href="{verification_url}" style="
                display: inline-block;
                padding: 12px 24px;
                background-color: #8B5CF6;
                color: white;
                text-decoration: none;
                border-radius: 4px;
                font-weight: bold;
              ">Подтвердить Email</a>
            </p>
            <p>Или перейдите по данной ссылке:</p>
            <p>{verification_url}</p>
            <p><small>Ссылка действительна в течение 24 часов.</small></p>
          </body>
        </html>
        """

        text = f"""
        Здравствуйте, {username}!
        
        Спасибо за регистрацию.
        
        Для завершения регистрации подтвердите ваш email, перейдя по ссылке:
        {verification_url}
        
        Ссылка действительна в течение 24 часов.
        """

        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)

        try:
            smtp_client = aiosmtplib.SMTP(
                hostname=self.smtp_server,
                port=self.smtp_port,
                use_tls=self.use_tls
            )
            
            await smtp_client.connect()
            if self.use_tls:
                await smtp_client.starttls()
            
            await smtp_client.login(self.sender_email, self.sender_password)
            await smtp_client.send_message(msg)
            await smtp_client.quit()
            
            return True
        except Exception as e:
            print(f"Ошибка отправки email: {e}")
            return False
        
    def send_verification_email_background(self, background_tasks: BackgroundTasks, to_email: str, verification_token: str, username: str):
        background_tasks.add_task(
            self.send_verification_email,
            to_email, verification_token, username
        )

    async def send_password_reset_email(self, to_email: str, password_reset_token: str, username: str) -> bool:
        password_reset_url = f"https://domen.co/password-reset-form?token={password_reset_token}"

        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Domen - восстановление пароля"
        msg['From'] = self.sender_email
        msg['To'] = to_email

        html = f"""
        <html>
          <body>
            <h2>Здравствуйте, {username}!</h2>
            <p>Мы получили запрос на изменение пароля от вашего аккаунта.</p>
            <p>Для изменения пароля нажмите на кнопку ниже:</p>
            <p>
              <a href="{password_reset_url}" style="
                display: inline-block;
                padding: 12px 24px;
                background-color: #8B5CF6;
                color: white;
                text-decoration: none;
                border-radius: 4px;
                font-weight: bold;
              ">Изменить пароль</a>
            </p>
            <p>Или перейдите по данной ссылке:</p>
            <p>{password_reset_url}</p>
            <p><small>Ссылка действительна в течение 24 часов.</small></p>
          </body>
        </html>
        """

        text = f"""
        Здравствуйте, {username}!
        
        Мы получили запрос на изменение пароля от вашего аккаунта.

        Для изменения пароля перейдите по ссылке:
        {password_reset_url}
        
        Ссылка действительна в течение 24 часов.
        """

        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)

        try:
            smtp_client = aiosmtplib.SMTP(
                hostname=self.smtp_server,
                port=self.smtp_port,
                use_tls=self.use_tls
            )
            
            await smtp_client.connect()
            if self.use_tls:
                await smtp_client.starttls()
            
            await smtp_client.login(self.sender_email, self.sender_password)
            await smtp_client.send_message(msg)
            await smtp_client.quit()
            
            return True
        except Exception as e:
            print(f"Ошибка отправки email: {e}")
            return False

    def send_password_reset_email_background(self, background_tasks: BackgroundTasks, to_email: str, password_reset_token: str, username: str):
        background_tasks.add_task(
            self.send_password_reset_email,
            to_email, password_reset_token, username
        )
        
email_service = EmailService()