"""Pure Decimal measurement rules; no resources, network, model or eval."""
import copy
from decimal import Decimal, InvalidOperation, localcontext
import re
import unicodedata


def compact(text):
    return re.sub(r'\s+', '', str(text))


def matching(text):
    return compact(unicodedata.normalize('NFKC',str(text)))


def original_token(source, start, end):
    normalized=[];positions=[]
    for index,char in enumerate(source):
        for value in unicodedata.normalize('NFKC',char):
            if not value.isspace():normalized.append(value);positions.append(index)
    if ''.join(normalized)!=matching(source):raise ValueError('Unsupported normalization mapping')
    return source[positions[start]:positions[end-1]+1]


def decimal_text(value):
    value=Decimal(value)
    if not value.is_finite() or value<0:raise ValueError('Invalid nonnegative measurement')
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


class MeasurementRules:
    def __init__(self, value):
        self.data=copy.deepcopy(value)
        self.version=value['version']
        if not isinstance(self.version,str) or not self.version:raise ValueError('Rule version missing')
        for family in ('area_units','length_units'):
            if not isinstance(value[family],dict) or not value[family]:raise ValueError('Unit rules missing')
            for alias,item in value[family].items():
                if not isinstance(alias,str) or not alias or set(item)!={'unit','factor'}:raise ValueError('Invalid unit rule')
                if not isinstance(item['unit'],str) or not item['unit'] or not isinstance(item['factor'],str) or not re.fullmatch(r'\d+(?:\.\d+)?',item['factor']):raise ValueError('Invalid unit definition')
                factor=Decimal(item['factor'])
                if not factor.is_finite() or factor<=0 or len(item['factor'])>32:raise ValueError('Invalid conversion')
        for field in ('area_markers','negative_terms','suspected_terms','aggregate_terms','approximate_terms','unsupported_dimension_terms','product_operators'):
            if not isinstance(value[field],list) or not value[field] or any(not isinstance(v,str) or not v for v in value[field]):raise ValueError('Invalid vocabulary')
        if value['product_operators']!=['×','乘以','*']:raise ValueError('Only bounded multiplication operators supported')
        for k in ('max_expression_chars','max_number_chars'):
            if type(value[k]) is not int or not 1<=value[k]<=1000:raise ValueError('Invalid rule bound')
        if set(value['type_aliases'])!={'pothole','exposed_rebar','deck_pavement','girder'}:raise ValueError('Taxonomy extension requires a contract change')
        for aliases in value['type_aliases'].values():
            if not isinstance(aliases,list) or not aliases or any(not isinstance(a,str) or not a for a in aliases):raise ValueError('Invalid type alias')
        self.number=r'\d+(?:\.\d+)?'
        self.area_pattern=self._alternatives(value['area_units'])
        self.length_pattern=self._alternatives(value['length_units'])
        self.marker_pattern=self._alternatives(value['area_markers'])
        self.operator_pattern=self._alternatives(value['product_operators'])
        self.units={family:{matching(alias).lower():item for alias,item in value[family].items()}
                    for family in ('area_units','length_units')}
        for family in self.units:
            for alias,item in value[family].items():
                if self.units[family][matching(alias).lower()]!=item:raise ValueError('Conflicting normalized unit aliases')

    @staticmethod
    def _alternatives(values):
        return '(?:'+'|'.join(re.escape(matching(x)) for x in sorted(values,key=len,reverse=True))+')'

    def unit(self, value, family='area_units'):
        return self.units[family].get(matching(value).lower())

    def has(self, text, field):
        return any(term in text for term in self.data[field])

    def type_supported(self, kind, text):
        return any(word in text for word in self.data['type_aliases'].get(kind,[]))

    def qualifiers(self,text):
        counts=re.findall(r'(?<!\d)(\d+|[一二三四五六七八九十百千零两]+)处',matching(text))
        multiple=any((int(c)!=1 if c.isdecimal() else c!='一') for c in counts)
        return {'negative':self.has(text,'negative_terms'),'suspected':self.has(text,'suspected_terms'),
                'aggregate':self.has(text,'aggregate_terms') or multiple,
                'approximate':self.has(text,'approximate_terms')}

    def direct(self, text, *, header=None):
        """Recognize paired values/units, never arbitrary numbers from a whole row."""
        if len(text)>self.data['max_expression_chars']:return []
        text=matching(text)
        pattern=rf'{self.marker_pattern}(?:为|是|[:：=])?(?:大约|约|近似)?({self.number})({self.area_pattern})(?![A-Za-z0-9²])'
        matches=[(m[1],m[2]) for m in re.finditer(pattern,text,re.I)]
        if header and any(mark in header for mark in self.data['area_markers']):
            full=re.fullmatch(rf'({self.number})({self.area_pattern})?',text,re.I)
            if full:
                units=re.findall(self.area_pattern,matching(header),re.I)
                hinted=list(dict.fromkeys(units))
                if full[2] or len(hinted)==1:matches.append((full[1],full[2] or hinted[0]))
        return list(dict.fromkeys((v,u) for v,u in matches if len(v)<=self.data['max_number_chars']))

    def calculate(self, data):
        """Return value/conversion/basis/derivation or None when unsupported."""
        expression=data.get('area_expression')
        original=data.get('area_original_value');unit=data.get('area_original_unit')
        with localcontext() as context:
            context.prec=100
            if expression is None:
                spec=self.unit(unit)
                if original is None or spec is None or not re.fullmatch(self.number,original) or len(original)>self.data['max_number_chars']:return None
                value=Decimal(original)*Decimal(spec['factor'])
                stated=self.direct(data.get('description',''))
                if any(Decimal(v)*Decimal(self.unit(u)['factor'])!=value for v,u in stated):
                    raise ValueError('Conflicting area in own description')
                method='identity' if Decimal(spec['factor'])==1 else 'unit_scale'
                return self._result(value,method,'reported',[(original,unit,spec['factor'])],None)
            if original is not None or unit is not None:raise ValueError('Derived area cannot pretend to be an original scalar')
            if not isinstance(expression,str) or len(expression)>self.data['max_expression_chars']:return None
            text=matching(expression)
            if self.has(text,'unsupported_dimension_terms'):return None
            product=rf'{self.marker_pattern}(?:为|是|[:：=])?(?:约)?({self.number}){self.operator_pattern}({self.number})({self.area_pattern})(?![A-Za-z0-9²])'
            products=list(re.finditer(product,text,re.I))
            dimensional_product=rf'{self.marker_pattern}(?:为|是|[:：=])?(?:约)?({self.number})({self.length_pattern}){self.operator_pattern}({self.number})({self.length_pattern})(?![A-Za-z0-9²])'
            dimensions=list(re.finditer(dimensional_product,text,re.I))
            if dimensions:
                if len(dimensions)!=1 or products:return None
                m=dimensions[0]
                if re.search(r'[×*~～至]|乘以',text[m.end():]):return None
                if max(len(m[1]),len(m[3]))>self.data['max_number_chars']:return None
                ua,ub=original_token(expression,m.start(2),m.end(2)),original_token(expression,m.start(4),m.end(4))
                sa,sb=self.unit(ua,'length_units'),self.unit(ub,'length_units')
                value=Decimal(m[1])*Decimal(sa['factor'])*Decimal(m[3])*Decimal(sb['factor'])
                result=self._result(value,'explicit_area_product','explicit_area_product',[(m[1],ua,sa['factor']),(m[3],ub,sb['factor'])],expression)
            elif len(products)==1:
                m=products[0]
                # Do not accept a suffix of a three-factor or range expression.
                if re.search(r'[×*~～至]|乘以',text[m.end():]):return None
                v1,v2=m[1],m[2];u=original_token(expression,m.start(3),m.end(3));spec=self.unit(u)
                if max(len(v1),len(v2))>self.data['max_number_chars']:return None
                value=Decimal(v1)*Decimal(v2)*Decimal(spec['factor'])
                result=self._result(value,'explicit_area_product','explicit_area_product',[(v1,u,spec['factor']),(v2,'dimensionless','1')],expression)
            else:
                if products or '矩形' not in text:return None
                lengths=list(re.finditer(rf'(?<![\w])长(?:为|[:：=])?(?:约)?({self.number})({self.length_pattern})(?![A-Za-z0-9²])',text,re.I))
                widths=list(re.finditer(rf'宽(?:为|[:：=])?(?:约)?({self.number})({self.length_pattern})(?![A-Za-z0-9²])',text,re.I))
                # Chinese labels next to prose are common; keep a numeric/unit boundary instead of \w.
                if not lengths:lengths=list(re.finditer(rf'长(?:为|[:：=])?(?:约)?({self.number})({self.length_pattern})(?![A-Za-z0-9²])',text,re.I))
                if len(lengths)!=1 or len(widths)!=1:return None
                a,b=lengths[0],widths[0]
                if max(len(a[1]),len(b[1]))>self.data['max_number_chars']:return None
                ua,ub=original_token(expression,a.start(2),a.end(2)),original_token(expression,b.start(2),b.end(2))
                sa,sb=self.unit(ua,'length_units'),self.unit(ub,'length_units')
                value=Decimal(a[1])*Decimal(sa['factor'])*Decimal(b[1])*Decimal(sb['factor'])
                result=self._result(value,'rectangle_product','rectangle_dimensions',[(a[1],ua,sa['factor']),(b[1],ub,sb['factor'])],expression)
            # If the same source reports an area, do not hide disagreement.
            explicit=self.direct(expression)
            if any(Decimal(v)*Decimal(self.unit(u)['factor'])!=Decimal(result['area_value']) for v,u in explicit):return None
            return result

    def _result(self,value,method,basis,inputs,expression):
        return {'area_value':decimal_text(value),'area_conversion':method,'area_basis':basis,
                'area_derivation':{'method':method,'inputs':[{'value':v,'unit':u,'factor':f} for v,u,f in inputs],
                                  'result_value':decimal_text(value),'result_unit':'m²','rules_version':self.version},
                'area_expression':expression,'area_rules_version':self.version}


def legacy_area(data):
    value=data.get('area_original_value')
    if value is None or compact(data.get('area_original_unit') or '') not in {'m²','㎡','m2'}:return None
    try:
        decimal=Decimal(value)
        if not decimal.is_finite() or decimal<0:return None
        return {'area_value':format(decimal,'f'),'area_conversion':'identity'}
    except (InvalidOperation,ValueError,TypeError):return None


def record_area_valid(record,rules=None):
    try:
        if record.get('area_unit')!='m²':return False
        if record.get('area_rules_version') is not None:
            if rules is None or record['area_rules_version']!=rules.version:return False
            expected=rules.calculate(record)
            return expected is not None and all(record.get(k)==v for k,v in expected.items())
        expected=legacy_area(record)
        return expected is not None and record.get('area_conversion')=='identity' and Decimal(expected['area_value'])==Decimal(record['area_value'])
    except (ValueError,TypeError,KeyError,InvalidOperation):return False
