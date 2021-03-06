#!/usr/bin/env python
# -*- coding: utf-8 -*-

import numpy as np
from skimage import io, data, filter, transform, morphology, feature
from xml.dom import minidom
from scipy import signal

def sqdist(p0, p1):
    dx = p0[0] - p1[0]
    dy = p0[1] - p1[1]
    return dx*dx + dy*dy

## Lee una imagen para procesar por OCR
#
# @param file           path a la imagen
def load_image(file):
    # TODO: forzar escala de grises

    # lee la imagen
    img = data.load(file)

    # la binariza en caso de que sea escala de grises
    if not img.dtype == 'bool':
        thr = filter.threshold_otsu(img)
        img = img > thr

    #si la proporcion de pixels en blanco es mayor a la mitad, la invierte
    if img.sum() > 0.5 * img.size:
        img = np.bitwise_not(img);

    return img

## Estimación del angulo de rotacion del formulario
#
# @param img                imagen binaria
# @param processing_scale   factor de escala que aplico a la imagen antes del procesamiento
def estimate_rotation(img):
    assert(img.dtype == 'bool')

    # elimina bloques rellenos para acelerar la deteccion de lineas
    elem = morphology.square(2)
    aux = morphology.binary_dilation(img, elem) - morphology.binary_erosion(img, elem)

    # Detección de lineas usando transformada de Hough probabilística
    thres = 50
    minlen = 0.1 * min(aux.shape)
    maxgap = 0.01 * minlen
    lines = transform.probabilistic_hough(aux, threshold=thres, line_length=minlen, line_gap=maxgap)

    # me aseguro que el primer punto de cada línea sea el más próximo al origen
    for lin in lines:
        (x0,y0), (x1,y1) = lin
        if x1*x1+y1*y1 < x0*x0+y0*y0:
            (x0, x1) = (x1, x0)
            (y0, y1) = (y1, y0)

    # orientación dominante
    angle_half_range = np.math.pi / 4
    nbins = int(2 * angle_half_range * (180./np.math.pi) / 0.2)

    orient = []
    for lin in lines:
        (x0,y0), (x1,y1) = lin
        orient.append(np.math.atan2(y1-y0, x1-x0))

    (h, binval) = np.histogram(orient, range=(-angle_half_range, angle_half_range), bins=nbins)
    alpha = binval[h.argmax()] * (180./ np.math.pi)
    return alpha + 0.5 * (binval[1] - binval[0]) * (180./ np.math.pi)

## Detección de lineas horizontales y verticales
#
# @param img            imagen
# @param simplify       si es True, se eliminan líneas redundates
def detect_lines(img, simplify=False):
    minsize = min(img.shape)
    maxsize = max(img.shape)

    # Detección de lineas usando transformada de Hough probabilística
    minlen = 0.1 * minsize
    maxgap = 0.1 * minlen
    angles = np.array([0, np.math.pi/2]) # asume imagen rectificada
    lines = transform.probabilistic_hough(img, theta=angles, threshold=10, line_length=minlen, line_gap=maxgap)

    # separa líneas verticales y horizontales
    vlines = []
    hlines = []
    for lin in lines:
        p0, p1 = lin
        (x0, y0) = p0
        (x1, y1) = p1
        linlen = np.math.sqrt(sqdist(p0, p1))
        if linlen > minsize:
            continue
        xc, yc = 0.5*(x0+x1), 0.5*(y0+y1)
        lin_info = [x0, y0, x1, y1, xc, yc, linlen]
        if x0 == x1:
            if y1 > y0:
                lin_info[1], lin_info[3] = lin_info[3], lin_info[1]
            vlines.append(lin_info)
        else:
            if x1 > x0:
                lin_info[0], lin_info[2] = lin_info[2], lin_info[0]
            hlines.append(lin_info)

    # filtrado de líneas duplicadas
    if simplify:
        dthr = 0.01 * minsize
        dthr = dthr * dthr

        for lines in (vlines, hlines):
            i = 0
            while i < len(lines):
                l1 = lines[i]

                acc = np.array(l1)
                nacc = 1.0

                j = i+1
                while j < len(lines):
                    l2 = lines[j]
                    d1 = sqdist(l1[0:2], l2[0:2])
                    d2 = sqdist(l1[2:4], l2[2:4])
                    # ambos extremos están muy próximos entre si
                    if d1 < dthr and d2 < dthr:
                        acc = acc + np.array(l2)
                        nacc = nacc + 1.0
                        lines.pop(j)
                    else:
                        j = j+1

                lines[i] = (acc/nacc).tolist()
                lines[i][4] = 0.5 * (lines[i][0]+lines[i][2])
                lines[i][5] = 0.5 * (lines[i][1]+lines[i][3])
                lines[i][6] =  np.math.sqrt(sqdist((lines[i][0],lines[i][1]), (lines[i][2],lines[i][3])))
                i = i+1

    # # ordena por longitud decreciente
    # vlines = sorted(vlines, key=lambda a_entry: a_entry[6])
    # vlines = vlines[::-1]
    # hlines = sorted(hlines, key=lambda a_entry: a_entry[6])
    # hlines = hlines[::-1]

    return hlines, vlines

## Detección de palabra clave
#
# @param img            imagen
# @param template       patch de referencia
def detect_keypatch(img, template):
    simg = feature.match_template(img, template, pad_input=True)
    simg = simg.clip(0, simg.max())
    rel_thr = 0.75
    peaks = feature.peak_local_max(simg, num_peaks=1, threshold_abs=rel_thr*(simg.max()-simg.min()), exclude_border=False)
    ht, wt = template.shape
    for i in range(len(peaks)):
        peaks[i] = [peaks[i][1]-wt/2, peaks[i][0]-ht/2]

    return peaks

## Extracción de "quads" a partir de líneas horizontales y verticales
#
# @param svg_file        modelo
'''
     (A)     HL0        (B)
      +------------------+
      |
      |
      |
      | VL0
      |
      |
      |      HL1
      +------------------+
     (C)                (D)
'''
def detect_quads(hlines, vlines):

    dthr = 10 #0.1 * np.min(vlines[:][6])
    dthr = dthr*dthr

    quads = []

    for vl0 in vlines:
        vl0_p0 = vl0[0:2]

        hl0_hyp = []
        for hl0 in hlines:
            hl0_p0 = hl0[0:2]
            if sqdist(vl0_p0, hl0_p0) < dthr:
                hl0_hyp.append(hl0)

        if len(hl0_hyp)==0:
            continue

        vl0_p1 = vl0[2:4]

        hl1_hyp = []
        for hl1 in hlines:
            hl1_p0 = hl1[0:2]
            if sqdist(vl0_p1, hl1_p0) < dthr:
                hl1_hyp.append(hl1)

        if len(hl1_hyp)==0:
            continue

        for vl1 in vlines:
            vl1_p0 = vl1[0:2]
            vl1_p1 = vl1[2:4]
            for hl0 in hl0_hyp:
                hl0_p1 = hl0[2:4]
                if sqdist(vl1_p0, hl0_p1) < dthr:
                    for hl1 in hl1_hyp:
                        hl1_p1 = hl1[2:4]
                        if sqdist(vl1_p1, hl1_p1) < dthr:
                            quads.append(vl0_p0 + vl1_p1)
                            break

    for q in quads:
        if q[0] > q[2]:
            q[0], q[2] = q[2], q[0]
        if q[1] > q[3]:
            q[1], q[3] = q[3], q[1]

    return quads

## Lectura de modelo a partir de archivo .svg
#
# @param svg_file        modelo
def parse_model(svg_file):
    doc = minidom.parse(svg_file)
    tables = []
    cells = []
    x0, y0 = 0., 0.
    for rect in doc.getElementsByTagName('rect'):
        x = float(rect.getAttribute('x'))
        y = float(rect.getAttribute('y'))
        width = float(rect.getAttribute('width'))
        height = float(rect.getAttribute('height'))
        label = rect.getAttribute('inkscape:label').lstrip()
        id = rect.getAttribute('id').lstrip()
        if label=="REFERENCIA":
            x0, y0 = x, y
        elif label.find("TABLA") == 0:
            tables.append([x, y, width, height, id])
        elif label.find("CELDA") == 0:
            cells.append([x, y, width, height, id])

    image = doc.getElementsByTagName('image')
    image_cx = float(image[0].getAttribute('x'))
    image_cy = float(image[0].getAttribute('y'))

    x0 = x0 - image_cx
    y0 = y0 - image_cy

    # refiere todo al patch de referencia
    for i in range(len(tables)):
        tables[i][0] = tables[i][0] - image_cx - x0
        tables[i][1] = tables[i][1] - image_cy - y0

    for i in range(len(cells)):
        cells[i][0] = cells[i][0] - image_cx - x0
        cells[i][1] = cells[i][1] - image_cy - y0

    return tables, cells

## Procesa la celda para mandar al OCR
#
# @param sugimg          imagen de la celda
def process_cell(img):

    # la binariza en caso de que sea escala de grises
    if not img.dtype == 'bool':
        img = img > 0  # Binarizar

    # Calcular máscaras para limpiar lineas largas verticales
    h_k = 0.8
    sum0 = np.sum(img, 0)  # Aplastar la matriz a una fila con las sumas de los valores de cada columna.
    thr0 = sum0 < h_k * img.shape[0]
    thr0 = thr0.reshape(len(thr0), 1) # Convertirlo a vector de una dimensión

    # Calcular máscaras para limpiar lineas largas horizontales
    w_k = 0.5
    sum1 = np.sum(img, 1)
    thr1 = sum1 < w_k * img.shape[1]
    thr1 = thr1.reshape(len(thr1), 1)

    mask = thr0.transpose() * thr1 # Generar máscara final para la celda
    mask_lines = mask.copy()

    elem = morphology.square(5)
    mask = morphology.binary_erosion(mask, elem) # Eliminar ruido

    img1 = np.bitwise_and(mask, img) # Imagen filtrada

    # segmentación del bloque de números
    kerw = 5  # Kernel width
    thr_k = 0.8

    # Calcular mascara para marcar inicio y fin de región con dígitos horizontalmente
    sum0 = np.sum(img1, 0)
    sum0 = signal.medfilt(sum0, kerw)
    thr0 = sum0 > thr_k * np.median(sum0)
    thr0 = np.bitwise_and(thr0.cumsum() > 0, np.flipud(np.flipud(thr0).cumsum() > 0))
    thr0 = thr0.reshape(len(thr0), 1)

    # Calcular mascara para marcar inicio y fin de región con dígitos verticalmente
    sum1 = np.sum(img1, 1)
    sum1 = signal.medfilt(sum1, kerw)
    thr1 = sum1 > thr_k * np.median(sum1)
    thr1 = np.bitwise_and(thr1.cumsum() > 0, np.flipud(np.flipud(thr1).cumsum() > 0))
    thr1 = thr1.reshape(len(thr1), 1)

    # Mascara final para inicio y fin de caracteres (bounding box of digit region)
    mask = thr0.transpose() * thr1
    mask = morphology.binary_dilation(mask, morphology.square(2))


    img = np.bitwise_and(mask_lines.astype(img.dtype), img)  # Aplicar máscara para quitar lineas
    img = morphology.binary_dilation(img, morphology.disk(1)) # Dilatación para unir números quebrados por la máscara anterior
    img = morphology.binary_erosion(img, morphology.disk(1)) # Volver a la fomorma 'original' con los bordes unidos

    return np.bitwise_and(mask, img)

## Segmentación de dígitos (computa bounding-boxes)
#
# @param img             imagen procesada
def segment_digits(img):

    # la binariza en caso de que sea escala de grises
    if not img.dtype == 'bool':
        img = img > 0

    min_size = 32
    medfilt_k = 5

    img0 = morphology.remove_small_objects(img, min_size=min_size)

    sum1 = np.sum(img0, 1)
    sum1 = signal.medfilt(sum1, medfilt_k) # Suavizado del perfil acumulado
    bp1 = sum1 > 0

    # Obtener coordenada en y de los puntos inicio y fin de los dígitos (asumiendo una sola línea de dígitos)
    idx_top = [i for i in range(len(bp1)) if bp1[i]>0]
    idx_bottom = [len(bp1)-i+1 for i in range(len(bp1)) if bp1[len(bp1)-i-1]>0]
    if len(idx_top) > 0 and len(idx_bottom) > 0:
        bp1[idx_top[0]:idx_bottom[0]+1] = True

    sum0 = np.sum(img0, 0)
    sum0 = signal.medfilt(sum0, medfilt_k)
    bp0 = sum0 > 0

    # Obtener coordenada en x de los puntos inicio y fin de los dígitos
    idx_01_transition = [i for i in range(1, len(bp0)) if bp0[i-1]==False and bp0[i]==True]
    idx_10_transition = [i for i in range(len(bp0)-1) if bp0[i]==True and bp0[i+1]==False]
    bb=[]
    if len(idx_01_transition)==len(idx_10_transition):
        for i in range(len(idx_01_transition)):
            bb.append([idx_01_transition[i], idx_top[0], idx_10_transition[i], idx_bottom[0]])

    return bb


# ----------------------------------------------------------------------

import os, sys
import matplotlib.pyplot as plt
import matplotlib.cm as cm

PATH = os.path.dirname(os.path.abspath(__file__))

#image_file = path+'/040240351_7634.pbm'
#image_file = PATH+'/040010002_0052.pbm'
#image_file = path+'/030010001_0001.pbm'

keyword_file = PATH + '/templates/keyword.pbm'
model_file = PATH + '/templates/CordobaOct2013.svg'

def main():
    try:
        image_file = os.path.join(PATH, sys.argv[1])
        process_telegram(image_file)
    except Exception, e:
        print >>sys.stderr, "Uso: python telegrama/telegrama.py archivo_telegrama.\n"
        print e
        return 0

def process_telegram(image_file):
    # levanta imagen
    img1 = load_image(image_file)

    # achico la imagen para acelerar el procesamiento
    processing_scale = 0.5
    img2 = transform.rescale(img1, processing_scale)
    img2 = img2 > 0

    # operaciones morfológicas (preproc.)
    elem = morphology.square(2)
    #img2 = morphology.binary_dilation(img2, elem)
    img3 = morphology.remove_small_objects(img2, min_size=64, connectivity=8)

    # estimacion de orientación + rectificación
    alpha = estimate_rotation(img3)
    img4 = transform.rotate(img3, angle=alpha, resize=True)
    print '  alpha =', str(alpha * 180. / np.math.pi)

    # detección de lineas horiz y vert
    hlines, vlines = detect_lines(img4, False)
    print '  lines =', len(hlines) + len(vlines)

    # detección de la palabra TELEGRAMA
    keypatch = load_image(keyword_file)
    keypatch = transform.rescale(keypatch, processing_scale)
    peaks = detect_keypatch(img4, keypatch)
    hk, wk = keypatch.shape
    print '  keypatch coord =', (peaks[0][0], peaks[0][1])

    # cuadriláteros
    quads = detect_quads(hlines, vlines)
    print '  quads =', len(quads)

    # modelo de formulario
    tables, cells = parse_model(model_file)
    print '  svg tables =', len(tables), '/ cells =', len(cells)

    # coordenadas referidas al keyword detectado
    x0, y0 = peaks[0]
    for i in range(len(tables)):
        tables[i][0] = tables[i][0] * processing_scale + x0
        tables[i][1] = tables[i][1] * processing_scale + y0
        tables[i][2] = tables[i][2] * processing_scale
        tables[i][3] = tables[i][3] * processing_scale

    for i in range(len(cells)):
        cells[i][0] = cells[i][0] * processing_scale + x0
        cells[i][1] = cells[i][1] * processing_scale + y0
        cells[i][2] = cells[i][2] * processing_scale
        cells[i][3] = cells[i][3] * processing_scale

    # Overlap de cuadrilateros y modelo
    overlap = []
    for i in range(len(quads)):
        overlap.append([0] * len(tables))

    for i in range(len(quads)):
        x1q, y1q, x2q, y2q = quads[i][0:4]
        area_q = (y2q - y1q) * (x2q - x1q)
        for j in range(len(tables)):
            x1m, y1m, x2m, y2m = tables[j][0], tables[j][1], tables[j][0]+tables[j][2], tables[j][1]+tables[j][3]
            area_m = (y2m - y1m) * (x2m - x1m)

            x1_inter = max(x1q, x1m)
            x2_inter = min(x2q, x2m)
            y1_inter = max(y1q, y1m)
            y2_inter = min(y2q, y2m)

            if y2_inter > y1_inter and x2_inter > x1_inter:
                area_inter = (y2_inter - y1_inter) * (x2_inter - x1_inter)
                area_union = area_q + area_m - area_inter
                overlap[i][j] = np.math.sqrt(area_inter / area_union)

    # estimar escalado que alinea los quads detectados con el modelo
    min_match_overlap = 0.5
    xratio, yratio = [], []
    for i in range(len(quads)):
        x1q, y1q, x2q, y2q = quads[i][0:4]
        for j in range(len(tables)):
            x1m, y1m, x2m, y2m = tables[j][0], tables[j][1], tables[j][0]+tables[j][2]-1.0, tables[j][1]+tables[j][3]-1.0
            if overlap[i][j] > min_match_overlap:
                yratio.append((y1q-y0) / (y1m-y0))
                yratio.append((y2q-y0) / (y2m-y0))
                xratio.append((x1q-x0) / (x1m-x0))
                xratio.append((x2q-x0) / (x2m-x0))
    print '  overlaping quads =', len(xratio)

    median_xratio = 1.0
    if len(xratio) > 0:
        median_xratio = np.median(xratio)

    median_yratio = 1.0
    if len(yratio) > 0:
        median_yratio = np.median(yratio)

    for i in range(len(tables)):
        tables[i][0] = (tables[i][0] - x0) * median_xratio + x0
        tables[i][1] = (tables[i][1] - y0) * median_yratio + y0
        tables[i][2] = (tables[i][2] - x0) * median_xratio + x0
        tables[i][3] = (tables[i][3] - y0) * median_yratio + y0

    for i in range(len(cells)):
        cells[i][0] = (cells[i][0] - x0) * median_xratio + x0
        cells[i][1] = (cells[i][1] - y0) * median_yratio + y0
        cells[i][2] = (cells[i][2] - x0) * median_xratio + x0
        cells[i][3] = (cells[i][3] - y0) * median_yratio + y0

    # crop de celdas en img original
    base_img = img1
    if not base_img is img1:
        processing_scale = 1.0
    base_img = np.bitwise_not(base_img);
    base_name = image_file[:image_file.rfind(".")]

    # elem = morphology.square(2)
    # base_img = morphology.binary_dilation(base_img, elem)
    # base_img = morphology.binary_erosion(base_img, elem)

    # cropear celdas y tablas para guardar
    img_ext = '.jpg'
    for elem in (cells, tables):
        for n in range(len(elem)):
            x1, y1, w, h, id = elem[n]
            x1 = int(x1 / processing_scale)
            y1 = int(y1 / processing_scale)
            w = int(w / processing_scale)
            h = int(h / processing_scale)
            x2 = min(x1+w-1, base_img.shape[1]-1)
            y2 = min(y1+h-1, base_img.shape[0]-1)

            subimg = np.zeros([h, w], dtype='bool')
            for i in range(y1, y2+1):
                for j in range(x1, x2+1):
                    subimg[i-y1][j-x1] = base_img[i][j]

            subimg_name = base_name + '-' + elem[n][4]
            io.imsave(subimg_name + img_ext, subimg.astype('float64'))

            if elem[n] in cells:
                # limpia la celda tratando de dejar solo los números
                subimg = process_cell(np.bitwise_not(subimg))

                # segmentación de dígitos dentro de la subimagen
                bounding_boxes = segment_digits(subimg)

                # invierte antes de guardar
                subimg = np.bitwise_not(subimg)
                io.imsave(subimg_name + '-0' + img_ext, subimg.astype('float64'))

                for k in range(len(bounding_boxes)):
                    bb = bounding_boxes[k]
                    bbh, bbw = bb[3]-bb[1]+1, bb[2]-bb[0]+1
                    digit = np.zeros([bbh, bbw], dtype='bool')
                    for i in range(bb[1], bb[3]+1):
                        for j in range(bb[0], bb[2]+1):
                            digit[i-bb[1]][j-bb[0]] = subimg[i][j]
                    io.imsave(subimg_name + '-' + str(k+1) + img_ext, digit.astype('float64'))


    # visualización
    fig, (ax1, ax2) = plt.subplots(ncols=2)

    ax1.imshow(img2, cmap=cm.Greys_r)
    ax1.set_axis_off()

    #palabra clave
    for pk in peaks:
        x, y = pk[0], pk[1]
        feat = plt.Rectangle((x, y), wk, hk, edgecolor='r', facecolor='none', linewidth=2)
        ax1.add_patch(feat)

    #quads
    for q in quads:
        rect = plt.Rectangle((q[0], q[1]), q[2]-q[0], q[3]-q[1], edgecolor='y', facecolor='none', linewidth=2)
        ax1.add_patch(rect)

    ax2.imshow(img2, cmap=cm.Greys_r)
    ax2.set_axis_off()

    #tablas
    x0, y0 = peaks[0]
    for field in tables:
        x, y = field[0], field[1]
        w, h = field[2], field[3]
        feat = plt.Rectangle((x, y), w, h, edgecolor='r', facecolor='none', linewidth=2)
        ax2.add_patch(feat)

    #celdas
    for field in cells:
        x, y = field[0], field[1]
        w, h = field[2], field[3]
        feat = plt.Rectangle((x, y), w, h, edgecolor='g', facecolor='none', linewidth=2)
        ax2.add_patch(feat)

    plt.savefig(base_name+'-PREVIEW.jpg', dpi=150)

if __name__ == "__main__":
    sys.exit(main())
