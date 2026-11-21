from pydantic import BaseModel, EmailStr, validator, Field
import re
from email_validator import validate_email, EmailNotValidError
from typing import Optional

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=25)
    email: EmailStr 

    @validator('email')
    def validate_email_domain(cls, v):
        try:
            email_info = validate_email(v, check_deliverability=True)
            return email_info.normalized
        except EmailNotValidError as e:
            raise ValueError(f"Некорректный email адрес: {str(e)}")

    password: str = Field(..., min_length=6)
    confirm_password: str
    agree_terms: bool
    
    @validator('username')
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Имя пользователя может содержать только буквы, цифры и подчеркивания')
        return v

    @validator('confirm_password')
    def validate_password(cls, confirm_pass, values):
        if 'password' in values and confirm_pass != values['password']:
            raise ValueError('Пароли не совпадают')
        return confirm_pass
    
    @validator('agree_terms')
    def validate_terms(cls, v):
        if not v:
            raise ValueError('Необходимо согласие с условиями использования')
        return v
    
class UserLogin(BaseModel):
    username_or_email: str = Field(..., min_length=3, max_length=25)
    password: str = Field(..., min_length=6)

