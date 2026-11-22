from asgiref.wsgi import WsgiToAsgi
from app import create_app
application = WsgiToAsgi(create_app())
