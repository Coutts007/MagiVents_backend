"""Server-generated ticket documents: the PDF ticket and the URL its QR code points to."""
import io
from pathlib import Path

from django.conf import settings
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

LOGO = Path(settings.BASE_DIR) / 'core' / 'assets' / 'magivents-logo-tile.png'

# Brand colours (frontend src/index.css)
ESPRESSO = HexColor('#1E1814')
MOCHA = HexColor('#675A50')
TERRACOTTA = HexColor('#8A4F33')
GOLD = HexColor('#C7B173')
SAND = HexColor('#E9E2D6')
IVORY = HexColor('#F4EFE7')
BORDER = HexColor('#D8CDBC')


def ticket_url(booking):
    """What the QR code encodes: the booking's UUID, never the short (guessable) ticket code."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/?ticket={booking.id}"


def format_kes(amount):
    return f'KSh {int(round(amount)):,}'


def payment_line(booking):
    if booking.payment_method == 'free' or booking.total_price == 0:
        return 'Free entry'
    method = {'mpesa': 'M-Pesa', 'card': 'Card', 'complimentary': 'Complimentary'}.get(booking.payment_method, 'Card')
    reference = f' · Ref {booking.mpesa_receipt_number}' if booking.mpesa_receipt_number else ''
    return f'{format_kes(booking.total_price)} paid via {method}{reference}'


def _qr_drawing(data, size):
    widget = QrCodeWidget(data, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def build_ticket_pdf(booking):
    """A one-page A4 ticket with the brand header, the event details and a scannable QR code."""
    g = booking.gathering
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f'MagiVents ticket {booking.ticket_code}')
    pdf.setAuthor('MagiVents')
    width, height = A4

    # Ticket card
    left, right = 20 * mm, width - 20 * mm
    top, bottom = height - 20 * mm, height - 215 * mm
    pdf.setFillColor(IVORY)
    pdf.setStrokeColor(BORDER)
    pdf.roundRect(left, bottom, right - left, top - bottom, 6 * mm, stroke=1, fill=1)

    # Header band with the logo and a gold rule
    band = 26 * mm
    pdf.setFillColor(ESPRESSO)
    pdf.roundRect(left, top - band, right - left, band, 6 * mm, stroke=0, fill=1)
    pdf.rect(left, top - band, right - left, 6 * mm, stroke=0, fill=1)
    pdf.setFillColor(GOLD)
    pdf.rect(left, top - band - 1.2 * mm, right - left, 1.2 * mm, stroke=0, fill=1)
    if LOGO.exists():
        pdf.drawImage(ImageReader(str(LOGO)), left + 8 * mm, top - band + 6 * mm, width=55 * mm, height=14 * mm,
                      preserveAspectRatio=True, anchor='w', mask='auto')
    pdf.setFillColor(GOLD)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawRightString(right - 8 * mm, top - 12 * mm, 'ADMISSION TICKET')
    pdf.setFillColor(IVORY)
    pdf.setFont('Helvetica', 8)
    pdf.drawRightString(right - 8 * mm, top - 17 * mm, "Kenya's events platform")

    # Event details
    x = left + 10 * mm
    y = top - band - 14 * mm
    pdf.setFillColor(TERRACOTTA)
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawString(x, y, g.category.upper())
    y -= 9 * mm
    pdf.setFillColor(ESPRESSO)
    qr_size = 52 * mm
    qr_x = right - 10 * mm - qr_size
    text_width = qr_x - x - 8 * mm
    title_size = 20 if len(g.title) <= 30 else 16
    pdf.setFont('Times-Bold', title_size)
    title_lines = simpleSplit(g.title, 'Times-Bold', title_size, text_width)[:2]
    for i, line in enumerate(title_lines):
        pdf.drawString(x, y - i * (title_size + 2), line)
    y -= (len(title_lines) - 1) * (title_size + 2)

    rows = [
        ('Ticket code', booking.ticket_code or '—'),
        ('Date & time', f'{g.date_display} · {g.time_display}'),
        ('Venue', ', '.join(filter(None, [g.venue_name, g.venue_address, g.venue_city]))),
        ('Attendee', f'{booking.attendee_name} ({booking.attendee_email})'),
        ('Ticket type', f'{booking.tier_name} · admits {booking.quantity} '
                        f'{"person" if booking.quantity == 1 else "people"}'),
        ('Payment', payment_line(booking)),
        ('Booked on', booking.booking_date.strftime('%d %b %Y')),
    ]
    y -= 12 * mm
    for label, value in rows:
        font, size = ('Helvetica-Bold', 12) if label == 'Ticket code' else ('Helvetica', 10)
        lines = simpleSplit(value, font, size, text_width)[:2] or ['']
        pdf.setFillColor(MOCHA)
        pdf.setFont('Helvetica', 7.5)
        pdf.drawString(x, y, label.upper())
        pdf.setFillColor(ESPRESSO)
        pdf.setFont(font, size)
        for i, line in enumerate(lines):
            pdf.drawString(x, y - 5 * mm - i * 4.5 * mm, line)
        y -= 13 * mm + (len(lines) - 1) * 4.5 * mm

    # QR code on the right
    qr_y = top - band - 16 * mm - qr_size
    pdf.setFillColor(HexColor('#FFFFFF'))
    pdf.setStrokeColor(BORDER)
    pdf.roundRect(qr_x - 3 * mm, qr_y - 3 * mm, qr_size + 6 * mm, qr_size + 6 * mm, 3 * mm, stroke=1, fill=1)
    renderPDF.draw(_qr_drawing(ticket_url(booking), qr_size), pdf, qr_x, qr_y)
    pdf.setFillColor(MOCHA)
    pdf.setFont('Helvetica', 8)
    pdf.drawCentredString(qr_x + qr_size / 2, qr_y - 9 * mm, 'Scan at the entrance')

    # Footer instructions
    pdf.setStrokeColor(BORDER)
    pdf.line(left + 10 * mm, bottom + 22 * mm, right - 10 * mm, bottom + 22 * mm)
    pdf.setFillColor(MOCHA)
    pdf.setFont('Helvetica', 8.5)
    pdf.drawString(left + 10 * mm, bottom + 15 * mm,
                   'Show this ticket on your phone or printed. Staff scan the QR code or check the ticket code.')
    pdf.drawString(left + 10 * mm, bottom + 9 * mm, f'Help: {settings.MAGIVENTS_CONTACT_EMAIL}')

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
