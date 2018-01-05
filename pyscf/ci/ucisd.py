#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
Unrestricted CISD
'''

import time
from functools import reduce
import numpy
from pyscf import lib
from pyscf.lib import logger
from pyscf.cc import uccsd
from pyscf.cc import uccsd_rdm
from pyscf.ci import cisd
from pyscf.cc.ccsd import _unpack_4fold

def make_diagonal(myci, eris):
    nocca = eris.nocca
    noccb = eris.noccb
    nmoa = eris.focka.shape[0]
    nmob = eris.focka.shape[1]
    nvira = nmoa - nocca
    nvirb = nmob - noccb
    jdiag_aa = numpy.zeros((nmoa,nmoa))
    jdiag_ab = numpy.zeros((nmoa,nmob))
    jdiag_bb = numpy.zeros((nmob,nmob))
    jdiag_aa[:nocca,:nocca] = numpy.einsum('iijj->ij', eris.oooo)
    jdiag_aa[:nocca,nocca:] = numpy.einsum('iijj->ij', eris.oovv)
    jdiag_aa[nocca:,:nocca] = jdiag_aa[:nocca,nocca:].T
    jdiag_ab[:nocca,:noccb] = numpy.einsum('iijj->ij', eris.ooOO)
    jdiag_ab[:nocca,noccb:] = numpy.einsum('iijj->ij', eris.ooVV)
    jdiag_ab[nocca:,:noccb] = numpy.einsum('iijj->ji', eris.OOvv)
    jdiag_bb[:noccb,:noccb] = numpy.einsum('iijj->ij', eris.OOOO)
    jdiag_bb[:noccb,noccb:] = numpy.einsum('iijj->ij', eris.OOVV)
    jdiag_bb[noccb:,:noccb] = jdiag_bb[:noccb,noccb:].T

    kdiag_aa = numpy.zeros((nmoa,nmoa))
    kdiag_bb = numpy.zeros((nmob,nmob))
    kdiag_aa[:nocca,:nocca] = numpy.einsum('ijji->ij', eris.oooo)
    kdiag_aa[:nocca,nocca:] = numpy.einsum('ijji->ij', eris.ovvo)
    kdiag_aa[nocca:,:nocca] = kdiag_aa[:nocca,nocca:].T
    kdiag_bb[:noccb,:noccb] = numpy.einsum('ijji->ij', eris.OOOO)
    kdiag_bb[:noccb,noccb:] = numpy.einsum('ijji->ij', eris.OVVO)
    kdiag_bb[noccb:,:noccb] = kdiag_bb[:noccb,noccb:].T

#    if eris.vvvv is not None and eris.vvVV is not None and eris.VVVV is not None:
#        def diag_idx(n):
#            idx = numpy.arange(n)
#            return idx * (idx + 1) // 2 + idx
#        jdiag_aa[nocca:,nocca:] = eris.vvvv[diag_idx(nvira)[:,None],diag_idx(nvira)]
#        jdiag_ab[nocca:,noccb:] = eris.vvVV[diag_idx(nvira)[:,None],diag_idx(nvirb)]
#        jdiag_bb[noccb:,noccb:] = eris.VVVV[diag_idx(nvirb)[:,None],diag_idx(nvirb)]
#        kdiag_aa[nocca:,nocca:] = lib.unpack_tril(eris.vvvv.diagonal())
#        kdiag_bb[noccb:,noccb:] = lib.unpack_tril(eris.VVVV.diagonal())

    jkdiag_aa = jdiag_aa - kdiag_aa
    jkdiag_bb = jdiag_bb - kdiag_bb

    mo_ea = eris.focka.diagonal()
    mo_eb = eris.fockb.diagonal()
    ehf = (mo_ea[:nocca].sum() + mo_eb[:noccb].sum()
           - jkdiag_aa[:nocca,:nocca].sum() * .5
           - jdiag_ab[:nocca,:noccb].sum()
           - jkdiag_bb[:noccb,:noccb].sum() * .5)

    dia_a = lib.direct_sum('a-i->ia', mo_ea[nocca:], mo_ea[:nocca])
    dia_a -= jkdiag_aa[:nocca,nocca:]
    dia_b = lib.direct_sum('a-i->ia', mo_eb[noccb:], mo_eb[:noccb])
    dia_b -= jkdiag_bb[:noccb,noccb:]
    e1diag_a = dia_a + ehf
    e1diag_b = dia_b + ehf

    e2diag_aa = lib.direct_sum('ia+jb->ijab', dia_a, dia_a)
    e2diag_aa += ehf
    e2diag_aa += jkdiag_aa[:nocca,:nocca].reshape(nocca,nocca,1,1)
    e2diag_aa -= jkdiag_aa[:nocca,nocca:].reshape(nocca,1,1,nvira)
    e2diag_aa -= jkdiag_aa[:nocca,nocca:].reshape(1,nocca,nvira,1)
    e2diag_aa += jkdiag_aa[nocca:,nocca:].reshape(1,1,nvira,nvira)

    e2diag_ab = lib.direct_sum('ia+jb->ijab', dia_a, dia_b)
    e2diag_ab += ehf
    e2diag_ab += jdiag_ab[:nocca,:noccb].reshape(nocca,noccb,1,1)
    e2diag_ab += jdiag_ab[nocca:,noccb:].reshape(1,1,nvira,nvirb)
    e2diag_ab -= jdiag_ab[:nocca,noccb:].reshape(nocca,1,1,nvirb)
    e2diag_ab -= jdiag_ab[nocca:,:noccb].T.reshape(1,noccb,nvira,1)

    e2diag_bb = lib.direct_sum('ia+jb->ijab', dia_b, dia_b)
    e2diag_bb += ehf
    e2diag_bb += jkdiag_bb[:noccb,:noccb].reshape(noccb,noccb,1,1)
    e2diag_bb -= jkdiag_bb[:noccb,noccb:].reshape(noccb,1,1,nvirb)
    e2diag_bb -= jkdiag_bb[:noccb,noccb:].reshape(1,noccb,nvirb,1)
    e2diag_bb += jkdiag_bb[noccb:,noccb:].reshape(1,1,nvirb,nvirb)

    return amplitudes_to_cisdvec(ehf, (e1diag_a, e1diag_b),
                                 (e2diag_aa, e2diag_ab, e2diag_bb))

def contract(myci, civec, eris):
    nocca = eris.nocca
    noccb = eris.noccb
    nmoa = eris.focka.shape[0]
    nmob = eris.fockb.shape[0]
    nvira = nmoa - nocca
    nvirb = nmob - noccb
    c0, (c1a,c1b), (c2aa,c2ab,c2bb) = \
            cisdvec_to_amplitudes(civec, (nmoa,nmob), (nocca,noccb))

    #:t2 += 0.5*einsum('ijef,abef->ijab', c2, eris.vvvv)
    #:eris_vvvv = ao2mo.restore(1, eris.vvvv, nvira)
    #:eris_vvVV = ucisd_slow._restore(eris.vvVV, nvira, nvirb)
    #:eris_VVVV = ao2mo.restore(1, eris.VVVV, nvirb)
    #:t2aa += lib.einsum('ijef,aebf->ijab', c2aa, eris_vvvv)
    #:t2bb += lib.einsum('ijef,aebf->ijab', c2bb, eris_VVVV)
    #:t2ab += lib.einsum('iJeF,aeBF->iJaB', c2ab, eris_vvVV)
    t2aa, t2ab, t2bb = myci._add_vvvv(None, (c2aa,c2ab,c2bb), eris)
    t2aa *= .25
    t2bb *= .25

    fooa = eris.focka[:nocca,:nocca]
    foob = eris.fockb[:noccb,:noccb]
    fova = eris.focka[:nocca,nocca:]
    fovb = eris.fockb[:noccb,noccb:]
    fvva = eris.focka[nocca:,nocca:]
    fvvb = eris.fockb[noccb:,noccb:]

    t0 = 0
    t1a = 0
    t1b = 0
    eris_oovv = _cp(eris.oovv)
    eris_ooVV = _cp(eris.ooVV)
    eris_OOvv = _cp(eris.OOvv)
    eris_OOVV = _cp(eris.OOVV)
    eris_ovvo = _cp(eris.ovvo)
    eris_ovVO = _cp(eris.ovVO)
    eris_OVVO = _cp(eris.OVVO)
    #:t2 += eris.oovv * c0
    t2aa += .25 * c0 * eris_ovvo.transpose(0,3,1,2)
    t2aa -= .25 * c0 * eris_ovvo.transpose(0,3,2,1)
    t2bb += .25 * c0 * eris_OVVO.transpose(0,3,1,2)
    t2bb -= .25 * c0 * eris_OVVO.transpose(0,3,2,1)
    t2ab += c0 * eris_ovVO.transpose(0,3,1,2)
    #:t0 += numpy.einsum('ijab,ijab', eris.oovv, c2) * .25
    t0 += numpy.einsum('iabj,ijab', eris_ovvo, c2aa) * .25
    t0 -= numpy.einsum('jabi,ijab', eris_ovvo, c2aa) * .25
    t0 += numpy.einsum('iabj,ijab', eris_OVVO, c2bb) * .25
    t0 -= numpy.einsum('jabi,ijab', eris_OVVO, c2bb) * .25
    t0 += numpy.einsum('iabj,ijab', eris_ovVO, c2ab)

    #:tmp = einsum('imae,mbej->ijab', c2, eris.ovvo)
    #:tmp = tmp - tmp.transpose(0,1,3,2)
    #:t2 += tmp - tmp.transpose(1,0,2,3)
    ovvo = eris_ovvo - eris_oovv.transpose(0,3,2,1)
    OVVO = eris_OVVO - eris_OOVV.transpose(0,3,2,1)
    t2aa += lib.einsum('imae,jbem->ijab', c2aa, ovvo)
    t2aa += lib.einsum('iMaE,jbEM->ijab', c2ab, eris_ovVO)
    t2bb += lib.einsum('imae,jbem->ijab', c2bb, OVVO)
    t2bb += lib.einsum('mIeA,meBJ->IJAB', c2ab, eris_ovVO)
    t2ab += lib.einsum('imae,meBJ->iJaB', c2aa, eris_ovVO)
    t2ab += lib.einsum('iMaE,MEBJ->iJaB', c2ab, OVVO)
    t2ab += lib.einsum('IMAE,jbEM->jIbA', c2bb, eris_ovVO)
    t2ab += lib.einsum('mIeA,jbem->jIbA', c2ab, ovvo)
    t2ab -= lib.einsum('iMeA,JMeb->iJbA', c2ab, eris_OOvv)
    t2ab -= lib.einsum('mIaE,jmEB->jIaB', c2ab, eris_ooVV)

    #:t1 += einsum('nf,nafi->ia', c1, eris.ovvo)
    t1a += numpy.einsum('nf,nfai->ia', c1a, eris_ovvo)
    t1a -= numpy.einsum('nf,nifa->ia', c1a, eris_oovv)
    t1b += numpy.einsum('nf,nfai->ia', c1b, eris_OVVO)
    t1b -= numpy.einsum('nf,nifa->ia', c1b, eris_OOVV)
    t1b += numpy.einsum('nf,nfai->ia', c1a, eris_ovVO)
    t1a += numpy.einsum('nf,iafn->ia', c1b, eris_ovVO)

    #:t1 -= 0.5*einsum('mnae,mnie->ia', c2, eris.ooov)
    eris_ovoo = _cp(eris.ovoo)
    eris_OVOO = _cp(eris.OVOO)
    eris_OVoo = _cp(eris.OVoo)
    eris_ovOO = _cp(eris.ovOO)
    t1a += lib.einsum('mnae,meni->ia', c2aa, eris_ovoo)
    t1b += lib.einsum('mnae,meni->ia', c2bb, eris_OVOO)
    t1a -= lib.einsum('nMaE,MEni->ia', c2ab, eris_OVoo)
    t1b -= lib.einsum('mNeA,meNI->IA', c2ab, eris_ovOO)
    #:tmp = einsum('ma,mbij->ijab', c1, eris.ovoo)
    #:t2 -= tmp - tmp.transpose(0,1,3,2)
    t2aa -= lib.einsum('ma,jbmi->jiba', c1a, eris_ovoo)
    t2bb -= lib.einsum('ma,jbmi->jiba', c1b, eris_OVOO)
    t2ab -= lib.einsum('ma,JBmi->iJaB', c1a, eris_OVoo)
    t2ab -= lib.einsum('MA,ibMJ->iJbA', c1b, eris_ovOO)

    #:#:t1 -= 0.5*einsum('imef,maef->ia', c2, eris.ovvv)
    #:eris_ovvv = _cp(eris.ovvv)
    #:eris_OVVV = _cp(eris.OVVV)
    #:eris_ovVV = _cp(eris.ovVV)
    #:eris_OVvv = _cp(eris.OVvv)
    #:t1a += lib.einsum('mief,mefa->ia', c2aa, eris_ovvv)
    #:t1b += lib.einsum('MIEF,MEFA->IA', c2bb, eris_OVVV)
    #:t1a += lib.einsum('iMfE,MEaf->ia', c2ab, eris_OVvv)
    #:t1b += lib.einsum('mIeF,meAF->IA', c2ab, eris_ovVV)
    #:#:tmp = einsum('ie,jeba->ijab', c1, numpy.asarray(eris.ovvv).conj())
    #:#:t2 += tmp - tmp.transpose(1,0,2,3)
    #:t2aa += lib.einsum('ie,mbae->imab', c1a, eris_ovvv)
    #:t2bb += lib.einsum('ie,mbae->imab', c1b, eris_OVVV)
    #:t2ab += lib.einsum('ie,MBae->iMaB', c1a, eris_OVvv)
    #:t2ab += lib.einsum('IE,maBE->mIaB', c1b, eris_ovVV)
    mem_now = lib.current_memory()[0]
    max_memory = max(0, lib.param.MAX_MEMORY - mem_now)
    if nvira > 0 and nocca > 0:
        blksize = max(int(max_memory*1e6/8/(nvira**2*nocca*2)), 2)
        for p0,p1 in lib.prange(0, nvira, blksize):
            ovvv = _cp(eris.ovvv[:,p0:p1]).reshape(nocca*(p1-p0),-1)
            ovvv = lib.unpack_tril(ovvv).reshape(nocca,p1-p0,nvira,nvira)
            t1a += lib.einsum('mief,mefa->ia', c2aa[:,:,p0:p1], ovvv)
            t2aa[:,:,p0:p1] += lib.einsum('mbae,ie->miba', ovvv, c1a)
            ovvv = None

    if nvirb > 0 and noccb > 0:
        blksize = max(int(max_memory*1e6/8/(nvirb**2*noccb*2)), 2)
        for p0,p1 in lib.prange(0, nvirb, blksize):
            OVVV = _cp(eris.OVVV[:,p0:p1]).reshape(noccb*(p1-p0),-1)
            OVVV = lib.unpack_tril(OVVV).reshape(noccb,p1-p0,nvirb,nvirb)
            t1b += lib.einsum('MIEF,MEFA->IA', c2bb[:,:,p0:p1], OVVV)
            t2bb[:,:,p0:p1] += lib.einsum('mbae,ie->miba', OVVV, c1b)
            OVVV = None

    if nvirb > 0 and nocca > 0:
        blksize = max(int(max_memory*1e6/8/(nvirb**2*nocca*2)), 2)
        for p0,p1 in lib.prange(0, nvira, blksize):
            ovVV = _cp(eris.ovVV[:,p0:p1]).reshape(nocca*(p1-p0),-1)
            ovVV = lib.unpack_tril(ovVV).reshape(nocca,p1-p0,nvirb,nvirb)
            t1b += lib.einsum('mIeF,meAF->IA', c2ab[:,:,p0:p1], ovVV)
            t2ab[:,:,p0:p1] += lib.einsum('maBE,IE->mIaB', ovVV, c1b)
            ovVV = None

    if nvira > 0 and noccb > 0:
        blksize = max(int(max_memory*1e6/8/(nvira**2*noccb*2)), 2)
        for p0,p1 in lib.prange(0, nvirb, blksize):
            OVvv = _cp(eris.OVvv[:,p0:p1]).reshape(noccb*(p1-p0),-1)
            OVvv = lib.unpack_tril(OVvv).reshape(noccb,p1-p0,nvira,nvira)
            t1a += lib.einsum('iMfE,MEaf->ia', c2ab[:,:,:,p0:p1], OVvv)
            t2ab[:,:,:,p0:p1] += lib.einsum('MBae,ie->iMaB', OVvv, c1a)
            OVvv = None

    #:t1  = einsum('ie,ae->ia', c1, fvv)
    t1a += lib.einsum('ie,ae->ia', c1a, fvva)
    t1b += lib.einsum('ie,ae->ia', c1b, fvvb)
    #:t1 -= einsum('ma,mi->ia', c1, foo)
    t1a -= lib.einsum('ma,mi->ia', c1a, fooa)
    t1b -= lib.einsum('ma,mi->ia', c1b, foob)
    #:t1 += einsum('imae,me->ia', c2, fov)
    t1a += numpy.einsum('imae,me->ia', c2aa, fova)
    t1a += numpy.einsum('imae,me->ia', c2ab, fovb)
    t1b += numpy.einsum('imae,me->ia', c2bb, fovb)
    t1b += numpy.einsum('miea,me->ia', c2ab, fova)

    #:tmp = einsum('ijae,be->ijab', c2, fvv)
    #:t2  = tmp - tmp.transpose(0,1,3,2)
    t2aa += lib.einsum('ijae,be->ijab', c2aa, fvva*.5)
    t2bb += lib.einsum('ijae,be->ijab', c2bb, fvvb*.5)
    t2ab += lib.einsum('iJaE,BE->iJaB', c2ab, fvvb)
    t2ab += lib.einsum('iJeA,be->iJbA', c2ab, fvva)
    #:tmp = einsum('imab,mj->ijab', c2, foo)
    #:t2 -= tmp - tmp.transpose(1,0,2,3)
    t2aa -= lib.einsum('imab,mj->ijab', c2aa, fooa*.5)
    t2bb -= lib.einsum('imab,mj->ijab', c2bb, foob*.5)
    t2ab -= lib.einsum('iMaB,MJ->iJaB', c2ab, foob)
    t2ab -= lib.einsum('mIaB,mj->jIaB', c2ab, fooa)

    #:tmp = numpy.einsum('ia,jb->ijab', c1, fov)
    #:tmp = tmp - tmp.transpose(0,1,3,2)
    #:t2 += tmp - tmp.transpose(1,0,2,3)
    t2aa += numpy.einsum('ia,jb->ijab', c1a, fova)
    t2bb += numpy.einsum('ia,jb->ijab', c1b, fovb)
    t2ab += numpy.einsum('ia,jb->ijab', c1a, fovb)
    t2ab += numpy.einsum('ia,jb->jiba', c1b, fova)

    t2aa = t2aa - t2aa.transpose(0,1,3,2)
    t2aa = t2aa - t2aa.transpose(1,0,2,3)
    t2bb = t2bb - t2bb.transpose(0,1,3,2)
    t2bb = t2bb - t2bb.transpose(1,0,2,3)

    #:t2 += 0.5*einsum('mnab,mnij->ijab', c2, eris.oooo)
    eris_oooo = _cp(eris.oooo)
    eris_OOOO = _cp(eris.OOOO)
    eris_ooOO = _cp(eris.ooOO)
    t2aa += lib.einsum('mnab,minj->ijab', c2aa, eris_oooo)
    t2bb += lib.einsum('mnab,minj->ijab', c2bb, eris_OOOO)
    t2ab += lib.einsum('mNaB,miNJ->iJaB', c2ab, eris_ooOO)

    #:t1 += fov * c0
    t1a += fova * c0
    t1b += fovb * c0
    #:t0  = numpy.einsum('ia,ia', fov, c1)
    t0 += numpy.einsum('ia,ia', fova, c1a)
    t0 += numpy.einsum('ia,ia', fovb, c1b)
    return amplitudes_to_cisdvec(t0, (t1a,t1b), (t2aa,t2ab,t2bb))

def amplitudes_to_cisdvec(c0, c1, c2):
    c1a, c1b = c1
    c2aa, c2ab, c2bb = c2
    nocca, nvira = c1a.shape
    noccb, nvirb = c1b.shape
    def trilidx(n):
        idx = numpy.tril_indices(n, -1)
        return idx[0] * n + idx[1]
    ooidxa = trilidx(nocca)
    vvidxa = trilidx(nvira)
    ooidxb = trilidx(noccb)
    vvidxb = trilidx(nvirb)
    size = (1, nocca*nvira, noccb*nvirb, nocca*noccb*nvira*nvirb,
            len(ooidxa)*len(vvidxa), len(ooidxb)*len(vvidxb))
    loc = numpy.cumsum(size)
    civec = numpy.empty(loc[-1])
    civec[0] = c0
    civec[loc[0]:loc[1]] = c1a.ravel()
    civec[loc[1]:loc[2]] = c1b.ravel()
    civec[loc[2]:loc[3]] = c2ab.ravel()
    lib.take_2d(c2aa.reshape(nocca**2,nvira**2), ooidxa, vvidxa, out=civec[loc[3]:loc[4]])
    lib.take_2d(c2bb.reshape(noccb**2,nvirb**2), ooidxb, vvidxb, out=civec[loc[4]:loc[5]])
    return civec

def cisdvec_to_amplitudes(civec, nmo, nocc):
    norba, norbb = nmo
    nocca, noccb = nocc
    nvira = norba - nocca
    nvirb = norbb - noccb
    nooa = nocca * (nocca-1) // 2
    nvva = nvira * (nvira-1) // 2
    noob = noccb * (noccb-1) // 2
    nvvb = nvirb * (nvirb-1) // 2
    size = (1, nocca*nvira, noccb*nvirb, nocca*noccb*nvira*nvirb,
            nooa*nvva, noob*nvvb)
    loc = numpy.cumsum(size)
    c0 = civec[0]
    c1a = civec[loc[0]:loc[1]].reshape(nocca,nvira)
    c1b = civec[loc[1]:loc[2]].reshape(noccb,nvirb)
    c2ab = civec[loc[2]:loc[3]].reshape(nocca,noccb,nvira,nvirb)
    c2aa = _unpack_4fold(civec[loc[3]:loc[4]], nocca, nvira)
    c2bb = _unpack_4fold(civec[loc[4]:loc[5]], noccb, nvirb)
    return c0, (c1a,c1b), (c2aa,c2ab,c2bb)

def to_fcivec(cisdvec, nmo, nocc):
    from pyscf import fci
    from pyscf.ci.gcisd import t2strs
    norba, norbb = nmo
    assert(norba == norbb)
    nocca, noccb = nocc
    nvira = norba - nocca
    nvirb = norbb - noccb
    c0, c1, c2 = cisdvec_to_amplitudes(cisdvec, nmo, nocc)
    c1a, c1b = c1
    c2aa, c2ab, c2bb = c2
    t1addra, t1signa = cisd.t1strs(norba, nocca)
    t1addrb, t1signb = cisd.t1strs(norbb, noccb)

    na = fci.cistring.num_strings(norba, nocca)
    nb = fci.cistring.num_strings(norbb, noccb)
    fcivec = numpy.zeros((na,nb))
    fcivec[0,0] = c0
    fcivec[t1addra,0] = c1a[::-1].T.ravel() * t1signa
    fcivec[0,t1addrb] = c1b[::-1].T.ravel() * t1signb
    c2ab = c2ab[::-1,::-1].transpose(2,0,3,1).reshape(nocca*nvira,-1)
    c2ab = numpy.einsum('i,j,ij->ij', t1signa, t1signb, c2ab)
    lib.takebak_2d(fcivec, c2ab, t1addra, t1addrb)

    if nocca > 1 and nvira > 1:
        ooidx = numpy.tril_indices(nocca, -1)
        vvidx = numpy.tril_indices(nvira, -1)
        c2aa = c2aa[ooidx][:,vvidx[0],vvidx[1]]
        t2addra, t2signa = t2strs(norba, nocca)
        fcivec[t2addra,0] = c2aa[::-1].T.ravel() * t2signa
    if noccb > 1 and nvirb > 1:
        ooidx = numpy.tril_indices(noccb, -1)
        vvidx = numpy.tril_indices(nvirb, -1)
        c2bb = c2bb[ooidx][:,vvidx[0],vvidx[1]]
        t2addrb, t2signb = t2strs(norbb, noccb)
        fcivec[0,t2addrb] = c2bb[::-1].T.ravel() * t2signb
    return fcivec

def from_fcivec(ci0, nmo, nocc):
    from pyscf import fci
    from pyscf.ci.gcisd import t2strs
    norba, norbb = nmo
    nocca, noccb = nocc
    nvira = norba - nocca
    nvirb = norbb - noccb
    t1addra, t1signa = cisd.t1strs(norba, nocca)
    t1addrb, t1signb = cisd.t1strs(norbb, noccb)

    na = fci.cistring.num_strings(norba, nocca)
    nb = fci.cistring.num_strings(norbb, noccb)
    ci0 = ci0.reshape(na,nb)
    c0 = ci0[0,0]
    c1a = ((ci0[t1addra,0] * t1signa).reshape(nvira,nocca).T)[::-1]
    c1b = ((ci0[0,t1addrb] * t1signb).reshape(nvirb,noccb).T)[::-1]

    c2ab = numpy.einsum('i,j,ij->ij', t1signa, t1signb, ci0[t1addra][:,t1addrb])
    c2ab = c2ab.reshape(nvira,nocca,nvirb,noccb).transpose(1,3,0,2)
    c2ab = c2ab[::-1,::-1]
    t2addra, t2signa = t2strs(norba, nocca)
    c2aa = (ci0[t2addra,0] * t2signa).reshape(nvira*(nvira-1)//2,-1).T
    c2aa = _unpack_4fold(c2aa[::-1], nocca, nvira)
    t2addrb, t2signb = t2strs(norbb, noccb)
    c2bb = (ci0[0,t2addrb] * t2signb).reshape(nvirb*(nvirb-1)//2,-1).T
    c2bb = _unpack_4fold(c2bb[::-1], noccb, nvirb)

    return amplitudes_to_cisdvec(c0, (c1a,c1b), (c2aa,c2ab,c2bb))


def make_rdm1(myci, civec=None, nmo=None, nocc=None):
    '''1-particle density matrix
    '''
    if civec is None: civec = myci.ci
    if nmo is None: nmo = myci.nmo
    if nocc is None: nocc = myci.nocc
    d1 = _gamma1_intermediates(myci, civec, nmo, nocc)
    return uccsd_rdm._make_rdm1(myci, d1, with_frozen=True)

def make_rdm2(myci, civec=None, nmo=None, nocc=None):
    '''2-particle density matrix in chemist's notation
    '''
    if civec is None: civec = myci.ci
    if nmo is None: nmo = myci.nmo
    if nocc is None: nocc = myci.nocc
    d1 = _gamma1_intermediates(myci, civec, nmo, nocc)
    d2 = _gamma2_intermediates(myci, civec, nmo, nocc)
    return uccsd_rdm._make_rdm2(myci, d1, d2, with_dm1=True, with_frozen=True)

def _gamma1_intermediates(myci, civec, nmo, nocc):
    nmoa, nmob = nmo
    nocca, noccb = nocc
    c0, c1, c2 = cisdvec_to_amplitudes(civec, nmo, nocc)
    c1a, c1b = c1
    c2aa, c2ab, c2bb = c2

    dova = c0 * c1a
    dovb = c0 * c1b
    dova += numpy.einsum('jb,ijab->ia', c1a.conj(), c2aa)
    dova += numpy.einsum('jb,ijab->ia', c1b.conj(), c2ab)
    dovb += numpy.einsum('jb,ijab->ia', c1b.conj(), c2bb)
    dovb += numpy.einsum('jb,jiba->ia', c1a.conj(), c2ab)

    dooa  =-numpy.einsum('ia,ka->ik', c1a.conj(), c1a)
    doob  =-numpy.einsum('ia,ka->ik', c1b.conj(), c1b)
    dooa -= numpy.einsum('ijab,ikab->jk', c2aa.conj(), c2aa) * .5
    dooa -= numpy.einsum('jiab,kiab->jk', c2ab.conj(), c2ab)
    doob -= numpy.einsum('ijab,ikab->jk', c2bb.conj(), c2bb) * .5
    doob -= numpy.einsum('ijab,ikab->jk', c2ab.conj(), c2ab)

    dvva  = numpy.einsum('ia,ic->ca', c1a, c1a.conj())
    dvvb  = numpy.einsum('ia,ic->ca', c1b, c1b.conj())
    dvva += numpy.einsum('ijab,ijac->cb', c2aa, c2aa.conj()) * .5
    dvva += numpy.einsum('ijba,ijca->cb', c2ab, c2ab.conj())
    dvvb += numpy.einsum('ijba,ijca->cb', c2bb, c2bb.conj()) * .5
    dvvb += numpy.einsum('ijab,ijac->cb', c2ab, c2ab.conj())
    return ((dooa, doob), (dova, dovb), (dova.conj().T, dovb.conj().T),
            (dvva, dvvb))

def _gamma2_intermediates(myci, civec, nmo, nocc):
    nmoa, nmob = nmo
    nocca, noccb = nocc
    c0, c1, c2 = cisdvec_to_amplitudes(civec, nmo, nocc)
    c1a, c1b = c1
    c2aa, c2ab, c2bb = c2

    goovv = c0 * c2aa * .5
    goOvV = c0 * c2ab
    gOOVV = c0 * c2bb * .5

    govvv = numpy.einsum('ia,ikcd->kadc', c1a.conj(), c2aa) * .5
    gOvVv = numpy.einsum('ia,ikcd->kadc', c1a.conj(), c2ab)
    goVvV = numpy.einsum('ia,kidc->kadc', c1b.conj(), c2ab)
    gOVVV = numpy.einsum('ia,ikcd->kadc', c1b.conj(), c2bb) * .5

    gooov = numpy.einsum('ia,klac->klic', c1a.conj(), c2aa) *-.5
    goOoV =-numpy.einsum('ia,klac->klic', c1a.conj(), c2ab)
    gOoOv =-numpy.einsum('ia,lkca->klic', c1b.conj(), c2ab)
    gOOOV = numpy.einsum('ia,klac->klic', c1b.conj(), c2bb) *-.5

    goooo = numpy.einsum('ijab,klab->ijkl', c2aa.conj(), c2aa) * .25
    goOoO = numpy.einsum('ijab,klab->ijkl', c2ab.conj(), c2ab)
    gOOOO = numpy.einsum('ijab,klab->ijkl', c2bb.conj(), c2bb) * .25
    gvvvv = numpy.einsum('ijab,ijcd->abcd', c2aa, c2aa.conj()) * .25
    gvVvV = numpy.einsum('ijab,ijcd->abcd', c2ab, c2ab.conj())
    gVVVV = numpy.einsum('ijab,ijcd->abcd', c2bb, c2bb.conj()) * .25

    goVoV = numpy.einsum('jIaB,kIaC->jCkB', c2ab.conj(), c2ab)
    gOvOv = numpy.einsum('iJbA,iKcA->JcKb', c2ab.conj(), c2ab)

    govvo = numpy.einsum('ijab,ikac->jcbk', c2aa.conj(), c2aa)
    govvo+= numpy.einsum('jIbA,kIcA->jcbk', c2ab.conj(), c2ab)
    goVvO = numpy.einsum('jIbA,IKAC->jCbK', c2ab.conj(), c2bb)
    goVvO+= numpy.einsum('ijab,iKaC->jCbK', c2aa.conj(), c2ab)
    gOVVO = numpy.einsum('ijab,ikac->jcbk', c2bb.conj(), c2bb)
    gOVVO+= numpy.einsum('iJaB,iKaC->JCBK', c2ab.conj(), c2ab)
    govvo+= numpy.einsum('ia,jb->ibaj', c1a.conj(), c1a)
    goVvO+= numpy.einsum('ia,jb->ibaj', c1a.conj(), c1b)
    gOVVO+= numpy.einsum('ia,jb->ibaj', c1b.conj(), c1b)

    dovov = goovv.transpose(0,2,1,3) - goovv.transpose(0,3,1,2)
    doooo = goooo.transpose(0,2,1,3) - goooo.transpose(0,3,1,2)
    dvvvv = gvvvv.transpose(0,2,1,3) - gvvvv.transpose(0,3,1,2)
    dovvo = govvo.transpose(0,2,1,3)
    dooov = gooov.transpose(0,2,1,3) - gooov.transpose(1,2,0,3)
    dovvv = govvv.transpose(0,2,1,3) - govvv.transpose(0,3,1,2)
    doovv =-dovvo.transpose(0,3,2,1)
    dvvov = None

    dOVOV = gOOVV.transpose(0,2,1,3) - gOOVV.transpose(0,3,1,2)
    dOOOO = gOOOO.transpose(0,2,1,3) - gOOOO.transpose(0,3,1,2)
    dVVVV = gVVVV.transpose(0,2,1,3) - gVVVV.transpose(0,3,1,2)
    dOVVO = gOVVO.transpose(0,2,1,3)
    dOOOV = gOOOV.transpose(0,2,1,3) - gOOOV.transpose(1,2,0,3)
    dOVVV = gOVVV.transpose(0,2,1,3) - gOVVV.transpose(0,3,1,2)
    dOOVV =-dOVVO.transpose(0,3,2,1)
    dVVOV = None

    dovOV = goOvV.transpose(0,2,1,3)
    dooOO = goOoO.transpose(0,2,1,3)
    dvvVV = gvVvV.transpose(0,2,1,3)
    dovVO = goVvO.transpose(0,2,1,3)
    dooOV = goOoV.transpose(0,2,1,3)
    dovVV = goVvV.transpose(0,2,1,3)
    dooVV = goVoV.transpose(0,2,1,3)
    dooVV = -(dooVV + dooVV.transpose(1,0,3,2).conj()) * .5
    dvvOV = None

    dOVov = None
    dOOoo = None
    dVVvv = None
    dOVvo = dovVO.transpose(3,2,1,0).conj()
    dOOov = gOoOv.transpose(0,2,1,3)
    dOVvv = gOvVv.transpose(0,2,1,3)
    dOOvv = gOvOv.transpose(0,2,1,3)
    dOOvv =-(dOOvv + dOOvv.transpose(1,0,3,2).conj()) * .5
    dVVov = None

    return ((dovov, dovOV, dOVov, dOVOV),
            (dvvvv, dvvVV, dVVvv, dVVVV),
            (doooo, dooOO, dOOoo, dOOOO),
            (doovv, dooVV, dOOvv, dOOVV),
            (dovvo, dovVO, dOVvo, dOVVO),
            (dvvov, dvvOV, dVVov, dVVOV),
            (dovvv, dovVV, dOVvv, dOVVV),
            (dooov, dooOV, dOOov, dOOOV))


class UCISD(cisd.CISD):

    get_nocc = uccsd.get_nocc
    get_nmo = uccsd.get_nmo
    get_frozen_mask = uccsd.get_frozen_mask

    def get_init_guess(self, eris=None):
        if eris is None: eris = self.ao2mo(self.mo_coeff)
        nocca = eris.nocca
        noccb = eris.noccb
        mo_ea = eris.focka.diagonal()
        mo_eb = eris.fockb.diagonal()
        eia_a = mo_ea[:nocca,None] - mo_ea[None,nocca:]
        eia_b = mo_eb[:noccb,None] - mo_eb[None,noccb:]
        t1a = eris.focka[:nocca,nocca:] / eia_a
        t1b = eris.fockb[:noccb,noccb:] / eia_b

        eris_ovvo = _cp(eris.ovvo)
        eris_ovVO = _cp(eris.ovVO)
        eris_OVVO = _cp(eris.OVVO)
        t2aa = eris_ovvo.transpose(0,3,1,2) - eris_ovvo.transpose(0,3,2,1)
        t2bb = eris_OVVO.transpose(0,3,1,2) - eris_OVVO.transpose(0,3,2,1)
        t2ab = eris_ovVO.transpose(0,3,1,2).copy()
        t2aa /= lib.direct_sum('ia+jb->ijab', eia_a, eia_a)
        t2ab /= lib.direct_sum('ia+jb->ijab', eia_a, eia_b)
        t2bb /= lib.direct_sum('ia+jb->ijab', eia_b, eia_b)

        emp2  = numpy.einsum('ia,ia', eris.focka[:nocca,nocca:], t1a)
        emp2 += numpy.einsum('ia,ia', eris.fockb[:noccb,noccb:], t1b)
        emp2 += numpy.einsum('iabj,ijab', eris_ovvo, t2aa) * .25
        emp2 -= numpy.einsum('jabi,ijab', eris_ovvo, t2aa) * .25
        emp2 += numpy.einsum('iabj,ijab', eris_OVVO, t2bb) * .25
        emp2 -= numpy.einsum('jabi,ijab', eris_OVVO, t2bb) * .25
        emp2 += numpy.einsum('iabj,ijab', eris_ovVO, t2ab)
        self.emp2 = emp2
        logger.info(self, 'Init t2, MP2 energy = %.15g', self.emp2)

        if abs(emp2) < 1e-3 and (abs(t1a).sum()+abs(t1b).sum()) < 1e-3:
            t1a = 1e-1 / eia_a
            t1b = 1e-1 / eia_b
        return self.emp2, amplitudes_to_cisdvec(1, (t1a,t1b), (t2aa,t2ab,t2bb))

    contract = contract
    make_diagonal = make_diagonal
    _dot = None
    _add_vvvv = uccsd._add_vvvv

    def ao2mo(self, mo_coeff=None):
        nmoa, nmob = self.get_nmo()
        nao = self.mo_coeff[0].shape[0]
        nmo_pair = nmoa * (nmoa+1) // 2
        nao_pair = nao * (nao+1) // 2
        mem_incore = (max(nao_pair**2, nmoa**4) + nmo_pair**2) * 8/1e6
        mem_now = lib.current_memory()[0]
        if (self._scf._eri is not None and
            (mem_incore+mem_now < self.max_memory) or self.mol.incore_anyway):
            return uccsd._make_eris_incore(self, mo_coeff)

        elif hasattr(self._scf, 'with_df'):
            raise NotImplementedError

        else:
            return uccsd._make_eris_outcore(self, mo_coeff)

    def to_fcivec(self, cisdvec, nmo=None, nocc=None):
        return to_fcivec(cisdvec, nmo, nocc)

    def from_fcivec(self, fcivec, nmo=None, nocc=None):
        return from_fcivec(fcivec, nmo, nocc)

    def amplitudes_to_cisdvec(self, c0, c1, c2):
        return amplitudes_to_cisdvec(c0, c1, c2)

    def cisdvec_to_amplitudes(self, civec, nmo=None, nocc=None):
        if nmo is None: nmo = self.nmo
        if nocc is None: nocc = self.nocc
        return cisdvec_to_amplitudes(civec, nmo, nocc)

    make_rdm1 = make_rdm1
    make_rdm2 = make_rdm2

    def nuc_grad_method(self):
        from pyscf.ci import ucisd_grad
        return ucisd_grad.Gradients(self)

CISD = UCISD

def _cp(a):
    return numpy.array(a, copy=False, order='C')


if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    from pyscf import ao2mo

    mol = gto.Mole()
    mol.verbose = 0
    mol.atom = [
        ['O', ( 0., 0.    , 0.   )],
        ['H', ( 0., -0.757, 0.587)],
        ['H', ( 0., 0.757 , 0.587)],]
    mol.basis = {'H': 'sto-3g',
                 'O': 'sto-3g',}
    mol.build()
    mf = scf.UHF(mol).run(conv_tol=1e-14)
    myci = CISD(mf)
    eris = myci.ao2mo()
    ecisd, civec = myci.kernel(eris=eris)
    print(ecisd - -0.048878084082066106)

    nmoa = mf.mo_energy[0].size
    nmob = mf.mo_energy[1].size
    rdm1 = myci.make_rdm1(civec)
    rdm2 = myci.make_rdm2(civec)
    eri_aa = ao2mo.kernel(mf._eri, mf.mo_coeff[0], compact=False).reshape([nmoa]*4)
    eri_bb = ao2mo.kernel(mf._eri, mf.mo_coeff[1], compact=False).reshape([nmob]*4)
    eri_ab = ao2mo.kernel(mf._eri, [mf.mo_coeff[0], mf.mo_coeff[0],
                                    mf.mo_coeff[1], mf.mo_coeff[1]], compact=False)
    eri_ab = eri_ab.reshape(nmoa,nmoa,nmob,nmob)
    h1a = reduce(numpy.dot, (mf.mo_coeff[0].T, mf.get_hcore(), mf.mo_coeff[0]))
    h1b = reduce(numpy.dot, (mf.mo_coeff[1].T, mf.get_hcore(), mf.mo_coeff[1]))
    e2 = (numpy.einsum('ij,ji', h1a, rdm1[0]) +
          numpy.einsum('ij,ji', h1b, rdm1[1]) +
          numpy.einsum('ijkl,ijkl', eri_aa, rdm2[0]) * .5 +
          numpy.einsum('ijkl,ijkl', eri_ab, rdm2[1])      +
          numpy.einsum('ijkl,ijkl', eri_bb, rdm2[2]) * .5)
    print(ecisd + mf.e_tot - mol.energy_nuc() - e2)   # = 0

    print(abs(rdm1[0] - (numpy.einsum('ijkk->ij', rdm2[0]) +
                         numpy.einsum('ijkk->ij', rdm2[1]))/(mol.nelectron-1)).sum())
    print(abs(rdm1[1] - (numpy.einsum('ijkk->ij', rdm2[2]) +
                         numpy.einsum('kkij->ij', rdm2[1]))/(mol.nelectron-1)).sum())
