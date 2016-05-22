#!/usr/bin/env python
# -*- coding: utf-8 -*-

from email.parser import BytesParser
from email.utils import make_msgid
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from kittengroomer import FileBaseMem
from kittengroomer import KittenGroomerMailBase

import mimetypes
import olefile
import zipfile

# Prepare application/<subtype>
mimes_ooxml = ['vnd.openxmlformats-officedocument.']
mimes_office = ['msword', 'vnd.ms-']
mimes_libreoffice = ['vnd.oasis.opendocument']
mimes_rtf = ['rtf', 'richtext']
mimes_pdf = ['pdf', 'postscript']
mimes_xml = ['xml']
mimes_ms = ['dosexec']
mimes_compressed = ['zip', 'rar', 'bzip2', 'lzip', 'lzma', 'lzop',
                    'xz', 'compress', 'gzip', 'tar']
mimes_data = ['octet-stream']

# Prepare image/<subtype>
mimes_exif = ['image/jpeg', 'image/tiff']
mimes_png = ['image/png']

# Aliases
aliases = {
    # Win executables
    'application/x-msdos-program': 'application/x-dosexec',
    'application/x-dosexec': 'application/x-msdos-program',
    # Other apps with confusing mimetypes
    'application/rtf': 'text/rtf',
}

# Sometimes, mimetypes.guess_type is giving unexpected results, such as for the .tar.gz files:
# In [12]: mimetypes.guess_type('toot.tar.gz', strict=False)
# Out[12]: ('application/x-tar', 'gzip')
# It works as expected if you do mimetypes.guess_type('application/gzip', strict=False)
propertype = {'.gz': 'application/gzip'}

# Commonly used malicious extensions
# Sources: http://www.howtogeek.com/137270/50-file-extensions-that-are-potentially-dangerous-on-windows/
# https://github.com/wiregit/wirecode/blob/master/components/core-settings/src/main/java/org/limewire/core/settings/FilterSettings.java
mal_ext = (
    # Applications
    ".exe", ".pif", ".application", ".gadget", ".msi", ".msp", ".com", ".scr",
    ".hta", ".cpl", ".msc", ".jar",
    # Scripts
    ".bat", ".cmd", ".vb", ".vbs", ".vbe", ".js", ".jse", ".ws", ".wsf",
    ".wsc", ".wsh", ".ps1", ".ps1xml", ".ps2", ".ps2xml", ".psc1", ".psc2",
    ".msh", ".msh1", ".msh2", ".mshxml", ".msh1xml", ".msh2xml",
    # Shortcuts
    ".scf", ".lnk", ".inf",
    # Other
    ".reg", ".dll",
    # Office macro (OOXML with macro enabled)
    ".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppam",
    ".ppsm", ".sldm",
    # banned from wirecode
    ".asf", ".asx", ".au", ".htm", ".html", ".mht", ".vbs",
    ".wax", ".wm", ".wma", ".wmd", ".wmv", ".wmx", ".wmz", ".wvx",
)


class File(FileBaseMem):

    def __init__(self, file_obj, orig_filename):
        ''' Init file object, set the mimetype '''
        super(File, self).__init__(file_obj, orig_filename)

        self.is_recursive = False
        if not self.has_mimetype():
            # No mimetype, should not happen.
            self.make_dangerous()

        if not self.has_extension():
            self.make_dangerous()

        if self.extension in mal_ext:
            self.log_details.update({'malicious_extension': self.extension})
            self.make_dangerous()

        if self.is_dangerous():
            return

        self.log_details.update({'maintype': self.main_type,
                                 'subtype': self.sub_type,
                                 'extension': self.extension})

        # Check correlation known extension => actual mime type
        if propertype.get(self.extension) is not None:
            expected_mimetype = propertype.get(self.extension)
        else:
            expected_mimetype, encoding = mimetypes.guess_type(self.orig_filename, strict=False)
            if aliases.get(expected_mimetype) is not None:
                expected_mimetype = aliases.get(expected_mimetype)

        is_known_extension = self.extension in mimetypes.types_map.keys()
        if is_known_extension and expected_mimetype != self.mimetype:
            self.log_details.update({'expected_mimetype': expected_mimetype})
            self.make_dangerous()

        # check correlation actual mime type => known extensions
        if aliases.get(self.mimetype) is not None:
            mimetype = aliases.get(self.mimetype)
        else:
            mimetype = self.mimetype

        expected_extensions = mimetypes.guess_all_extensions(mimetype, strict=False)
        if expected_extensions:
            if len(self.extension) > 0 and self.extension not in expected_extensions:
                self.log_details.update({'expected_extensions': expected_extensions})
                self.make_dangerous()
        else:
            # there are no known extensions associated to this mimetype.
            pass


class KittenGroomerMail(KittenGroomerMailBase):

    def __init__(self, raw_email, max_recursive=2, debug=False):
        super(KittenGroomerMail, self).__init__(raw_email, debug)

        self.recursive = 0
        self.max_recursive = max_recursive

        subtypes_apps = [
            (mimes_office, self._winoffice),
            (mimes_ooxml, self._ooxml),
            (mimes_rtf, self.text),
            (mimes_libreoffice, self._libreoffice),
            (mimes_pdf, self._pdf),
            (mimes_xml, self.text),
            (mimes_ms, self._executables),
            (mimes_compressed, self._archive),
            (mimes_data, self._binary_app),
        ]
        self.subtypes_application = self._init_subtypes_application(subtypes_apps)

        self.mime_processing_options = {
            'text': self.text,
            'audio': self.audio,
            'image': self.image,
            'video': self.video,
            'application': self.application,
            'example': self.example,
            'message': self.message,
            'model': self.model,
            'multipart': self.multipart,
            'inode': self.inode,
        }

    def _init_subtypes_application(self, subtypes_application):
        '''
            Create the Dict to pick the right function based on the sub mime type
        '''
        to_return = {}
        for list_subtypes, fct in subtypes_application:
            for st in list_subtypes:
                to_return[st] = fct
        return to_return

    #######################

    def inode(self):
        ''' Usually empty file. No reason (?) to copy it on the dest key'''
        if self.cur_attachment.is_symlink():
            self.cur_attachment.log_string += 'Symlink to {}'.format(self.log_details['symlink'])
        else:
            self.cur_attachment.log_string += 'Inode file'

    def unknown(self):
        ''' This main type is unknown, that should not happen '''
        self.cur_attachment.log_string += 'Unknown file'

    def example(self):
        '''Used in examples, should never be returned by libmagic'''
        self.cur_attachment.log_string += 'Example file'

    def multipart(self):
        '''Used in web apps, should never be returned by libmagic'''
        self.cur_attachment.log_string += 'Multipart file'

    #######################

    def model(self):
        '''Way to process model file'''
        self.cur_attachment.log_string += 'Model file'
        self.cur_attachment.make_dangerous()

    #######################

    def message(self):
        '''Way to process message file'''
        # FIXME: process this one as recursive.
        self.cur_attachment.log_string += 'Message file'
        self.cur_attachment.make_dangerous()

    # ##### Converted ######
    def text(self):
        for r in mimes_rtf:
            if r in self.cur_attachment.sub_type:
                self.cur_attachment.log_string += 'Rich Text file'
                # TODO: need a way to convert it to plain text
                self.cur_attachment.force_ext('.txt')
                return
        for o in mimes_ooxml:
            if o in self.cur_attachment.sub_type:
                self.cur_attachment.log_string += 'OOXML File'
                self._ooxml()
                return
        self.cur_attachment.log_string += 'Text file'
        self.cur_attachment.force_ext('.txt')

    def application(self):
        ''' Everything can be there, using the subtype to decide '''
        for subtype, fct in self.subtypes_application.items():
            if subtype in self.cur_attachment.sub_type:
                fct()
                self.cur_attachment.log_string += 'Application file'
                return
        self.cur_attachment.log_string += 'Unknown Application file'
        self._unknown_app()

    def _executables(self):
        '''Way to process executable file'''
        self.cur_attachment.add_log_details('processing_type', 'executable')
        self.cur_attachment.make_dangerous()

    def _winoffice(self):
        # FIXME: oletools isn't compatible with python3, using olefile only
        self.cur_attachment.add_log_details('processing_type', 'WinOffice')
        # Try as if it is a valid document
        try:
            ole = olefile.OleFileIO(self.cur_attachment.file_obj, raise_defects=olefile.DEFECT_INCORRECT)
        except:
            self.cur_attachment.add_log_details('not_parsable', True)
            self.cur_attachment.make_dangerous()
        if ole.parsing_issues:
            self.cur_attachment.add_log_details('parsing_issues', True)
            self.cur_attachment.make_dangerous()
        else:
            if ole.exists('macros/vba') or ole.exists('Macros') \
                    or ole.exists('_VBA_PROJECT_CUR') or ole.exists('VBA'):
                self.cur_attachment.add_log_details('macro', True)
                self.cur_attachment.make_dangerous()

    def _ooxml(self):
        self.cur_attachment.add_log_details('processing_type', 'ooxml')
        # FIXME: officedissector can't process a pseudo file, skipping for now.

    def _libreoffice(self):
        self.cur_attachment.add_log_details('processing_type', 'libreoffice')
        # As long as there ar no way to do a sanity check on the files => dangerous
        try:
            lodoc = zipfile.ZipFile(self.cur_attachment.file_obj, 'r')
        except:
            self.cur_attachment.add_log_details('invalid', True)
            self.cur_attachment.make_dangerous()
        for f in lodoc.infolist():
            fname = f.filename.lower()
            if fname.startswith('script') or fname.startswith('basic') or \
                    fname.startswith('object') or fname.endswith('.bin'):
                self.cur_attachment.add_log_details('macro', True)
                self.cur_attachment.make_dangerous()

    def _pdf(self):
        '''Way to process PDF file'''
        self.cur_attachment.add_log_details('processing_type', 'pdf')
        # FIXME: PDFiD is... difficult and can't process a pseudo file, skipping for now.

    def _archive(self):
        '''Way to process Archive'''
        self.cur_attachment.add_log_details('processing_type', 'archive')
        # FIXME: We will unpack the archives later, not as simple as it seems if we want to do it in memory

    def _unknown_app(self):
        '''Way to process an unknown file'''
        self.cur_attachment.make_unknown()

    def _binary_app(self):
        '''Way to process an unknown binary file'''
        self.cur_attachment.make_binary()

    # ##### Not converted, checking the mime type ######
    def audio(self):
        '''Way to process an audio file'''
        self.cur_attachment.log_string += 'Audio file'
        self._media_processing()

    def image(self):
        '''Way to process an image'''
        self.cur_attachment.log_string += 'Image file'
        self._media_processing()
        self.cur_attachment.add_log_details('processing_type', 'image')

    def video(self):
        '''Way to process a video'''
        self.cur_attachment.log_string += 'Video file'
        self._media_processing()

    def _media_processing(self):
        '''Generic way to process all the media files'''
        self.cur_attachment.add_log_details('processing_type', 'media')

    #######################

    def reassemble_mail(self, to_keep, attachements):
        original_msgid = self.parsed_email.get_all('Message-ID')
        self.parsed_email.replace_header('Message-ID', make_msgid())
        if to_keep:
            if self.parsed_email.is_multipart():
                self.parsed_email.set_payload([to_keep[0]])
            else:
                self.parsed_email.set_payload(to_keep[0])
                return
        else:
            info_msg = MIMEText('Empty Message', _subtype='plain', _charset='utf-8')
            self.parsed_email.set_payload([info_msg])
        for k in to_keep[1:]:
            self.parsed_email.attach(k)
        info = 'The attachements of this mail have been sanitzed.\nOriginal Message-ID: {}'.format(original_msgid)
        info_msg = MIMEText(info, _subtype='plain', _charset='utf-8')
        info_msg.add_header('Content-Disposition', 'attachment', filename='Sanitized.txt')
        self.parsed_email.attach(info_msg)
        for f in attachements:
            processing_info = '{}'.format(f.log_details)
            processing_info_msg = MIMEText(processing_info, _subtype='plain', _charset='utf-8')
            processing_info_msg.add_header('Content-Disposition', 'attachment', filename='{}.log'.format(f.orig_filename))
            self.parsed_email.attach(processing_info_msg)
            msg = MIMEBase(f.main_type, f.sub_type)
            msg.set_payload(f.file_obj.getvalue())
            encoders.encode_base64(msg)
            msg.add_header('Content-Disposition', 'attachment', filename=f.orig_filename)
            self.parsed_email.attach(msg)

    def split_email(self, raw_email):
        self.parsed_email = BytesParser().parsebytes(raw_email)
        to_keep = []
        attachements = []
        if self.parsed_email.is_multipart():
            for p in self.parsed_email.get_payload():
                if p.get_filename():
                    attachements.append(File(p.get_payload(decode=True), p.get_filename()))
                else:
                    to_keep.append(p)
        else:
            to_keep.append(self.parsed_email.get_payload())
        return to_keep, attachements

    def process_mail(self, raw_email=None):
        if raw_email is None:
            raw_email = self.raw_email

        if self.recursive > 0:
            self._print_log()

        if self.recursive >= self.max_recursive:
            self.cur_attachment.make_dangerous()
            self.cur_attachment.add_log_details('Archive Bomb', True)
            self.log_name.warning('ARCHIVE BOMB.')
            self.log_name.warning('The content of the archive contains recursively other archives.')
            self.log_name.warning('This is a bad sign so the archive is not extracted to the destination key.')
        else:
            to_keep, attachements = self.split_email(raw_email)
            for f in attachements:
                self.cur_attachment = f
                self.log_name.info('Processing {} ({}/{})', self.cur_attachment.orig_filename,
                                   self.cur_attachment.main_type, self.cur_attachment.sub_type)
                if not self.cur_attachment.is_dangerous():
                    pass
            self.reassemble_mail(to_keep, attachements)

if __name__ == '__main__':
    import glob
    for f in glob.glob('/tmp/foobar/*'):
        t = KittenGroomerMail(open(f, 'rb').read())
        t.process_mail()
        print(f)
        #print(t.parsed_email.as_string())