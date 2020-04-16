from autode.config import Config
from autode.log import logger
from rdkit.Chem import AllChem
from autode.atoms import metals
from autode.species import Species
from autode.species import SolvatedSpecies
from autode.mol_graphs import make_graph
from autode.conformers.conformer import Conformer
from autode.conformers.conf_gen import get_simanl_atoms
from autode.conformers.conformers import conf_is_unique_rmsd
from autode.conformers.conformers import get_atoms_from_rdkit_mol_object
from autode.calculation import Calculation
from autode.solvent.explicit_solvent import do_explicit_solvent_qmmm
from autode.exceptions import NoAtomsInMolecule
from autode.utils import requires_atoms
from autode.smiles import init_organic_smiles
from autode.smiles import init_smiles
from multiprocessing import Pool


class Molecule(Species):

    def _init_smiles(self, smiles):
        """Initialise a molecule from a SMILES string using RDKit if it's purely organic"""
        """Initialise a molecule from a SMILES string """

        if any(metal in smiles for metal in metals):
            init_smiles(self, smiles)

        else:
            init_organic_smiles(self, smiles)

        logger.info(f'Initialisation with SMILES successful. Charge={self.charge}, Multiplicity={self.mult}, '
                    f'Num. Atoms={self.n_atoms}')
        return None

    @requires_atoms()
    def _generate_conformers(self, n_rdkit_confs=300, n_siman_confs=300):
        """
        Use a simulated annealing approach to generate conformers for this molecule.

        Keyword Arguments:
            n_rdkit_confs (int):
            n_siman_confs (int):
        """
        self.conformers = []

        if self.smiles is not None and self.rdkit_conf_gen_is_fine:
            logger.info(f'Using RDKit to generate conformers. {n_rdkit_confs} requested')

            method = AllChem.ETKDGv2()
            method.pruneRmsThresh = 0.5
            method.numThreads = Config.n_cores

            logger.info('Running conformation generation with RDKit... running')
            conf_ids = list(AllChem.EmbedMultipleConfs(self.rdkit_mol_obj, numConfs=n_rdkit_confs, params=method))
            logger.info('                                          ... done')

            conf_atoms_list = [get_atoms_from_rdkit_mol_object(self.rdkit_mol_obj, conf_id) for conf_id in conf_ids]

        else:
            logger.info('Using simulated annealing to generate conformers')
            with Pool(processes=Config.n_cores) as pool:
                results = [pool.apply_async(get_simanl_atoms, (self, None, i)) for i in range(n_siman_confs)]
                conf_atoms_list = [res.get(timeout=None) for res in results]

        for i, atoms in enumerate(conf_atoms_list):
            conf = Conformer(name=f'{self.name}_conf{i}', charge=self.charge, mult=self.mult, atoms=atoms)

            # If the conformer is unique on an RMSD threshold
            if conf_is_unique_rmsd(conf, self.conformers):
                conf.solvent = self.solvent
                self.conformers.append(conf)

        logger.info(f'Generated {len(self.conformers)} unique conformer(s)')
        return None

    @requires_atoms()
    def optimise(self, method):
        logger.info(f'Running optimisation of {self.name}')

        opt = Calculation(name=f'{self.name}_opt', molecule=self, method=method,
                          keywords_list=method.keywords.opt, n_cores=Config.n_cores, opt=True)
        opt.run()
        self.energy = opt.get_energy()
        self.set_atoms(atoms=opt.get_final_atoms())
        self.print_xyz_file(filename=f'{self.name}_optimised_{method.name}.xyz')

        return None

    def __init__(self, name='molecule', smiles=None, atoms=None, solvent_name=None, charge=0, mult=1):
        """Initialise a Molecule object.
        Will generate atoms lists of all the conformers found by simulated annealing within the number
        of conformers searched (n_confs)

        Keyword Arguments:
            name (str): Name of the molecule (default: {'molecule'})
            smiles (str): Standard SMILES string. e.g. generated by Chemdraw (default: {None})
            atoms (list(autode.atoms.Atom)): List of atoms in the species (default: {None})
            solvent_name (str): Solvent that the molecule is immersed in (default: {None})
            charge (int): Charge on the molecule (default: {0})
            mult (int): Spin multiplicity on the molecule (default: {1})
        """
        logger.info(f'Generating a Molecule object for {name}')
        super().__init__(name, atoms, charge, mult, solvent_name)

        # TODO init from xyzs?

        self.smiles = smiles
        self.rdkit_mol_obj = None
        self.rdkit_conf_gen_is_fine = True

        self.conformers = None

        if smiles:
            self._init_smiles(smiles)
        else:
            make_graph(self)

        if self.n_atoms == 0:
            raise NoAtomsInMolecule


class SolvatedMolecule(Molecule, SolvatedSpecies):

    @requires_atoms()
    def optimise(self, method):
        logger.info(f'Running optimisation of {self.name}')

        opt = Calculation(name=f'{self.name}_opt', molecule=self, method=method,
                          keywords_list=method.keywords.opt, n_cores=Config.n_cores, opt=True)
        opt.run()
        self.energy = opt.get_energy()
        self.set_atoms(atoms=opt.get_final_atoms())
        self.print_xyz_file(filename=f'{self.name}_optimised_{method.name}.xyz')
        for i, charge in enumerate(opt.get_atomic_charges()):
            self.graph.nodes[i]['charge'] = charge

        _, species_atoms, qm_solvent_atoms, mm_solvent_atoms = do_explicit_solvent_qmmm(self, self.solvent_mol, method, n_confs=96)
        self.set_atoms(species_atoms)
        self.qm_solvent_atoms = qm_solvent_atoms
        self.mm_solvent_atoms = mm_solvent_atoms

        return None

    def __init__(self, name='solvated_molecule', smiles=None, atoms=None, solvent_name=None, charge=0, mult=1):
        super().__init__(name, smiles, atoms, solvent_name, charge, mult)

        self.solvent_mol = None
        self.qm_solvent_atoms = None
        self.mm_solvent_atoms = None


class Reactant(Molecule):
    pass


class Product(Molecule):
    pass
