# -*- coding: utf-8 -*-
from django.http import HttpResponse
from django.core import serializers
#from django.utils import simplejson

from models import Telegram


def get_telegram_image_url(request, section, circuit, table):
	""" Devuelve url de la imagen completa del telegrama """
	telegram = Telegram.objects.get(section=section, circuit=circuit, table=table)
	return telegram.get_telegram_image_url()


def get_cell_image(request, section, circuit, table, table_id, cell_id):
	""" Devuelve la url de imagen de una celda del telegrama """
	telegram = Telegram.objects.get(section=section, circuit=circuit, table=table)
	telegram.cells.get()


def parse_cell(telegram_id, cell_id):
	"""
	Devuelve lista con los valores reconocidos de la imagen
	El orden de precisión es descendente, osea, el primer valor es el mas probable
	"""

def telegram_detail(request, section, circuit=None, table=None):
    """Segun el descripor unico seccion-circuito-mesa obtener el telegrama 
    correspondiente serializado a json.
    Si falta table o table y circuit devuelve los listados correspondientes.
    """
    telegrams = Telegram.objects.filter(section=section)
    if circuit:
        telegrams = telegrams.filter(circuit=circuit)
    if table:
        telegrams = telegrams.filter(table=table)
    jsondata = serializers.serialize('json', telegrams)

    #telegram = Telegram.objects.get(section=section, circuit=circuit, table=table)
    #jsondata = telegram  # simplejson.dump
    return HttpResponse(jsondata, mimetype='application/json')

def telegram_cell(request, section, circuit, table, tables, cell):
    """Segun el descripor unico seccion-circuito-mesa nombre de tabla y coordenadas obtener la celda 
    correspondiente serializado a json.
    """
    telegrams = Telegram.objects.filter(section=section, circuit=circuit, table=table)
    telegrams = telegrams.filter(tables__name=tables, tables__cells__position=cell)
    jsondata = serializers.serialize('json', telegrams)

    return HttpResponse(jsondata, mimetype='application/json')



